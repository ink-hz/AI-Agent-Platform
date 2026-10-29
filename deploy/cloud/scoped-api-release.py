#!/usr/bin/env python3
"""Scoped API image overlay and transaction. No SSH, credentials or Nginx operations."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time
import uuid

MIGRATION = 'backend/migrations/cloud_agent_sync/001_agent_sources.sql'
PROJECT = 'orbbec-agent-platform'
SERVICE = 'platform-api'
PRIVATE = Path('/opt/orbbec-agent-platform/private')
SHA = re.compile(r'[0-9a-f]{40}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
REFERENCE = re.compile(r'orbbec-agent-platform:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z')


class ReleaseError(Exception):
    pass


def require(condition, stage):
    if not condition:
        raise ReleaseError(stage)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def hostconfig_hash(value):
    """Docker's unique mount lists are unordered; keep every other field exact."""
    result = copy.deepcopy(value)
    targets = []
    if result.get('Binds') is not None:
        require(isinstance(result['Binds'], list), 'hostconfig_mounts')
        for bind in result['Binds']:
            require(isinstance(bind, str), 'hostconfig_mounts')
            parts = bind.split(':')
            require(len(parts) in (2, 3) and parts[1].startswith('/'), 'hostconfig_mounts')
            targets.append(parts[1])
        result['Binds'].sort()
    if result.get('Mounts') is not None:
        require(isinstance(result['Mounts'], list), 'hostconfig_mounts')
        for mount in result['Mounts']:
            require(isinstance(mount, dict) and isinstance(mount.get('Target'), str)
                    and mount['Target'].startswith('/'), 'hostconfig_mounts')
            targets.append(mount['Target'])
        result['Mounts'].sort(key=canonical)
    require(len(targets) == len(set(targets)), 'duplicate_mount_target')
    return digest(canonical(result))


def regular(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'regular_file')
    return path


def private_dir(path, create=False):
    path = Path(path)
    if create:
        path.mkdir(mode=0o700)  # Exclusive; never reuse an operator transaction.
    s = path.lstat()
    require(stat.S_ISDIR(s.st_mode) and not path.is_symlink()
            and stat.S_IMODE(s.st_mode) == 0o700 and s.st_uid == os.geteuid(), 'private_directory')
    return path


def private_read(path):
    path = regular(path)
    s = path.stat()
    require(s.st_uid == os.geteuid() and stat.S_IMODE(s.st_mode) == 0o600, 'private_file')
    return path.read_bytes()


def save(path, value):
    path = Path(path)
    raw = value if isinstance(value, bytes) else canonical(value) + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_json(path):
    return json.loads(private_read(path))


def command(args, cwd=None):
    # All command output is captured in memory; exceptions never include it.
    proc = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=900, env={**os.environ, 'COMPOSE_ANSI': 'never', 'DOCKER_BUILDKIT': '0'})
    require(proc.returncode == 0, 'command_failed')
    return proc.stdout


def docker(*args):
    require(os.geteuid() == 0, 'host_root')
    require(os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock') == 'unix:///var/run/docker.sock', 'docker_endpoint')
    require(not os.environ.get('DOCKER_CONTEXT'), 'docker_context')
    return command(['docker', '--host', 'unix:///var/run/docker.sock', *args])


def inspect_image(image):
    require(bool(IMAGE.fullmatch(image)), 'immutable_image')
    result = json.loads(docker('image', 'inspect', image))
    require(len(result) == 1 and result[0]['Id'] == image, 'image_identity')
    return result[0]


def application_reference(release, manifest_hash):
    require(bool(SHA.fullmatch(release)) and re.fullmatch(r'[0-9a-f]{64}', manifest_hash) is not None, 'image_reference')
    return 'orbbec-agent-platform:scoped-api-' + release + '-' + manifest_hash


def resolve_reference(reference, image_id):
    require(bool(REFERENCE.fullmatch(reference)) and bool(IMAGE.fullmatch(image_id)), 'image_reference')
    result = json.loads(docker('image', 'inspect', reference))
    require(len(result) == 1 and result[0]['Id'] == image_id
            and reference in (result[0].get('RepoTags') or []), 'image_reference')
    return result[0]


def assert_reference_bindings(state):
    require(state['new_image_reference'] == application_reference(state['release'], state['manifest_sha256']), 'image_reference')
    resolve_reference(state['old_image_reference'], state['old_image'])
    resolve_reference(state['new_image_reference'], state['new_image'])


def api_from_receipt(receipt):
    apis = [c for c in receipt['containers'] if c['compose_labels'].get('com.docker.compose.service') == SERVICE]
    require(len(apis) == 1 and bool(IMAGE.fullmatch(apis[0]['image_id'])), 'api_receipt')
    return apis[0]


def assert_static_baseline(proof, image, baseline):
    require(proof.get('image_id') == image and proof.get('baseline_commit') == baseline
            and proof.get('same_baseline') is True and proof.get('algorithm') == 'active-static-graph-v1'
            and re.fullmatch(r'[0-9a-f]{64}', proof.get('live_active_sha256', '')) is not None
            and proof['live_active_sha256'] == proof.get('baseline_active_sha256'), 'static_baseline')


def guardrails(receipt):
    # Hash reads only: never initialize/test/reload shared Nginx.
    files = []
    for item in receipt.get('nginx_config_files', []):
        path = Path(item['path'])
        require(path.is_file() and path.is_symlink() == item.get('symlink', False)
                and digest(path.read_bytes()) == item['sha256'], 'shared_config_changed')
    supplements = {unit['unit']: unit for unit in receipt.get('supplement', {}).get('admin', [])}
    for unit in receipt.get('systemd_units', []):
        if not unit['unit'].startswith('ai-admin-'):
            continue
        require(unit['unit'] in supplements and supplements[unit['unit']].get('configuration_files'), 'admin_config_baseline_missing')
        fields = 'LoadState,ActiveState,SubState,MainPID,Result,ActiveEnterTimestampMonotonic,InactiveEnterTimestampMonotonic,FragmentPath,DropInPaths'
        raw = command(['systemctl', 'show', '--no-pager', '--property=' + fields, unit['unit']])
        require(digest(raw) == unit['properties_sha256'], 'admin_process_changed')
        files.extend(unit.get('unit_files', []))
    for unit in receipt.get('supplement', {}).get('admin', []):
        files.extend(unit.get('configuration_files', []))
    for item in files:
        require(digest(regular(item['path']).read_bytes()) == item['sha256'], 'shared_config_changed')


def assert_release(repo, release):
    require(bool(SHA.fullmatch(release)), 'release_commit')
    head = command(['git', 'rev-parse', 'HEAD'], cwd=repo).decode().strip()
    origin = command(['git', 'rev-parse', 'refs/remotes/origin/master'], cwd=repo).decode().strip()
    require(head == origin == release, 'release_master')
    require(not command(['git', 'status', '--porcelain'], cwd=repo).strip(), 'release_dirty')


def stage(repo, baseline, release, receipt, code_proof, dist, output, static_proof):
    """Offline staging from committed Git objects plus an already built dist."""
    require(bool(SHA.fullmatch(baseline)) and bool(SHA.fullmatch(release)), 'commit')
    repo, dist, output = Path(repo), Path(dist), Path(output)
    assert_release(repo, release)
    command(['git', 'merge-base', '--is-ancestor', baseline, release], cwd=repo)
    api = api_from_receipt(receipt)
    assert_static_baseline(static_proof, api['image_id'], baseline)
    changed = command(['git', 'diff', '--no-renames', '--name-status', baseline, release, '--', 'backend/app', MIGRATION], cwd=repo).decode().splitlines()
    proof = {item['path']: item for item in code_proof}
    payload = []
    for row in changed:
        status, name = row.split('\t')
        require(status in ('A', 'M') and (name == MIGRATION or (name.startswith('backend/app/') and name.endswith('.py'))), 'delta_scope')
        require('..' not in PurePosixPath(name).parts, 'delta_scope')
        mode = command(['git', 'ls-tree', release, '--', name], cwd=repo).decode().split()[0]
        require(mode == '100644' or mode == '100755', 'delta_type')
        if status == 'M':
            old = command(['git', 'show', baseline + ':' + name], cwd=repo)
            item = proof.get(name, {})
            require(item.get('same_baseline') is True and item.get('actual') == digest(old)
                    and item.get('baseline') == digest(old), 'code_baseline')
        raw = command(['git', 'show', release + ':' + name], cwd=repo)
        payload.append((raw, '/app/backend/' + name.removeprefix('backend/')))
    require(any(dest == '/app/backend/' + MIGRATION.removeprefix('backend/') for _, dest in payload), 'migration_missing')
    require(dist.is_dir() and not dist.is_symlink() and (dist/'index.html').is_file(), 'dist')
    for path in sorted(dist.rglob('*')):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'dist_type')
        if path.is_file():
            payload.append((path.read_bytes(), '/app/backend/app/static/' + path.relative_to(dist).as_posix()))
    private_dir(output, create=True)
    (output/'payload').mkdir(mode=0o755)
    (output/'payload').chmod(0o755)
    files = []
    lines = ['FROM ' + api['image_id'], 'ENV PLATFORM_RELEASE_SHA=' + release]
    for raw, destination in payload:
        name = 'payload/' + destination.removeprefix('/app/backend/')
        (output/name).parent.mkdir(parents=True, exist_ok=True)
        save(output/name, raw)
        (output/name).chmod(0o644)
        files.append({'source': name, 'destination': destination, 'sha256': digest(raw), 'size': len(raw)})
    for directory in (output/'payload').rglob('*'):
        if directory.is_dir(): directory.chmod(0o755)
    lines.append('COPY --chown=10001:10001 ["payload/", "/app/backend/"]')
    dockerfile = ('\n'.join(lines) + '\n').encode()
    save(output/'Dockerfile', dockerfile)
    manifest = {'version': 1, 'baseline_commit': baseline, 'release_sha': release,
                'base_image_id': api['image_id'], 'files': files, 'dockerfile_sha256': digest(dockerfile),
                'dist_provenance': 'operator_built_from_release_commit; verify before staging'}
    save(output/'manifest.json', manifest)
    save(output/'readiness.json', receipt)
    save(output/'static-baseline.json', static_proof)
    return manifest


def validate_stage(root):
    root = private_dir(root)
    manifest = read_json(root/'manifest.json')
    require(manifest.get('version') == 1 and bool(SHA.fullmatch(manifest['release_sha']))
            and bool(IMAGE.fullmatch(manifest['base_image_id'])), 'manifest')
    require(digest(private_read(root/'Dockerfile')) == manifest['dockerfile_sha256'], 'artifact_hash')
    expected = {'Dockerfile', 'manifest.json', 'readiness.json', 'static-baseline.json'}
    assert_static_baseline(read_json(root/'static-baseline.json'), manifest['base_image_id'], manifest['baseline_commit'])
    for item in manifest['files']:
        relative = PurePosixPath(item['source'])
        destination = item['destination']
        require(not relative.is_absolute() and '..' not in relative.parts
                and item['source'] == 'payload/' + destination.removeprefix('/app/backend/')
                and (destination == '/app/backend/' + MIGRATION.removeprefix('backend/')
                     or destination.startswith('/app/backend/app/static/')
                     or (destination.startswith('/app/backend/app/') and destination.endswith('.py'))), 'artifact_path')
        path = regular(root/item['source'])
        metadata = path.stat()
        require(stat.S_IMODE(metadata.st_mode) == 0o644 and metadata.st_uid == os.geteuid(), 'artifact_mode')
        raw = path.read_bytes()
        require(digest(raw) == item['sha256'] and len(raw) == item['size'], 'artifact_hash')
        expected.add(item['source'])
    expected_dirs = set()
    for item in manifest['files']:
        expected_dirs.update(str(parent) for parent in PurePosixPath(item['source']).parents if str(parent) != '.')
    actual_dirs = set()
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'artifact_type')
        if path.is_dir():
            metadata = path.stat()
            require(stat.S_IMODE(metadata.st_mode) == 0o755 and metadata.st_uid == os.geteuid(), 'artifact_mode')
            actual_dirs.add(path.relative_to(root).as_posix())
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if not p.is_dir()}
    require(actual == expected and actual_dirs == expected_dirs, 'context_extra_files')
    return manifest


def assert_derived_image(base, derived, release):
    layers = base['RootFS']['Layers']
    require(derived['RootFS']['Layers'][:len(layers)] == layers
            and len(derived['RootFS']['Layers']) == len(layers) + 1
            and len(derived['RootFS']['Layers']) <= 128, 'image_layers')
    before, after = copy.deepcopy(base['Config']), copy.deepcopy(derived['Config'])
    env = dict(v.split('=', 1) for v in before.get('Env', []))
    env['PLATFORM_RELEASE_SHA'] = release
    require(dict(v.split('=', 1) for v in after.get('Env', [])) == env, 'image_environment')
    before.pop('Env', None); after.pop('Env', None)
    require(before == after, 'image_config')


def assert_parity(before, after, image, release):
    expected = copy.deepcopy(before)
    api = expected['services'][SERVICE]
    api['image'] = image
    api.setdefault('environment', {})['PLATFORM_RELEASE_SHA'] = release
    require(expected == after, 'compose_parity')


def daemon_logging():
    path = Path('/etc/docker/daemon.json')
    settings = json.loads(path.read_bytes()) if path.is_file() else {}
    expected = {'Type': settings.get('log-driver', 'json-file'), 'Config': settings.get('log-opts', {})}
    active = json.loads(docker('info', '--format', '{{json .LoggingDriver}}'))
    require(active == expected['Type'], 'daemon_logging')
    return expected


def duration_ns(value):
    match = re.fullmatch(r'(\d+)(ns|us|ms|s|m|h)', value)
    require(match is not None, 'unsupported_runtime_setting')
    return int(match[1]) * {'ns':1, 'us':1000, 'ms':1000000, 's':1000000000, 'm':60000000000, 'h':3600000000000}[match[2]]


def assert_runtime_policy(config, live, image, logging_default):
    """Bounded contract for this API. Unmapped Compose policy fails before mutation."""
    service = config['services'][SERVICE]
    allowed = {'image','environment','command','entrypoint','user','working_dir','volumes','networks',
               'depends_on','container_name','read_only','privileged','cap_add','cap_drop','security_opt',
               'ports','restart','logging','tmpfs','healthcheck'}
    require(set(service) <= allowed, 'unsupported_runtime_setting')
    host = live['HostConfig']
    expected_security = {'ReadonlyRootfs': bool(service.get('read_only', False)),
                         'Privileged': bool(service.get('privileged', False))}
    require(all(host.get(k) == v for k,v in expected_security.items()), 'live_security')
    for source, target in [('cap_add','CapAdd'),('cap_drop','CapDrop'),('security_opt','SecurityOpt')]:
        require(sorted(service.get(source) or []) == sorted(host.get(target) or []), 'live_security')
    masks = ['/proc/acpi','/proc/asound','/proc/interrupts','/proc/kcore','/proc/keys',
             '/proc/latency_stats','/proc/sched_debug','/proc/scsi','/proc/timer_list','/proc/timer_stats',
             '/sys/devices/virtual/powercap','/sys/firmware']
    readonly = ['/proc/bus','/proc/fs','/proc/irq','/proc/sys','/proc/sysrq-trigger']
    require(sorted(host.get('MaskedPaths', masks)) == sorted(masks)
            and sorted(host.get('ReadonlyPaths', readonly)) == sorted(readonly), 'live_security_defaults')
    require(not service.get('ports') and not host.get('PortBindings') and not host.get('PublishAllPorts')
            and all(not bindings for bindings in live['NetworkSettings'].get('Ports', {}).values()), 'live_ports')
    zeros = ['Memory','MemorySwap','MemoryReservation','NanoCpus','CpuShares','CpuQuota','CpuPeriod','OomScoreAdj',
             'BlkioWeight','CpuRealtimePeriod','CpuRealtimeRuntime','CpuCount','CpuPercent','IOMaximumIOps','IOMaximumBandwidth']
    require(all(host.get(k, 0) == 0 for k in zeros) and host.get('CpusetCpus') == ''
            and host.get('PidsLimit') is None, 'live_resources')
    defaults = {'IpcMode':'private','PidMode':'','UTSMode':'','CgroupnsMode':'private','ShmSize':67108864,
                'OomKillDisable':None,'AutoRemove':False,'Runtime':'runc','Isolation':'','Cgroup':'',
                'CpusetMems':'','MemorySwappiness':None,'ContainerIDFile':'','VolumeDriver':'','ConsoleSize':[0,0]}
    require(all(host.get(k, v) == v for k,v in defaults.items()), 'live_namespaces')
    for key in ['Ulimits','Devices','DeviceRequests','Dns','ExtraHosts','DnsOptions','DnsSearch',
                'DeviceCgroupRules','GroupAdd','Sysctls','Links','VolumesFrom','UsernsMode','CgroupParent',
                'BlkioWeightDevice','BlkioDeviceReadBps','BlkioDeviceWriteBps','BlkioDeviceReadIOps','BlkioDeviceWriteIOps']:
        require(not host.get(key), 'live_unsupported_policy')
    restart = service.get('restart', 'no')
    require(restart in ('no','always','unless-stopped'), 'unsupported_runtime_setting')
    require(host.get('RestartPolicy') == {'Name':restart, 'MaximumRetryCount':0}, 'live_restart')
    log = service.get('logging')
    expected_log = {'Type':log['driver'], 'Config':log.get('options', {})} if log else logging_default
    require(host.get('LogConfig') == expected_log, 'live_logging')
    tmpfs = {}
    for value in service.get('tmpfs', []):
        require(isinstance(value, str) and ':' in value, 'unsupported_runtime_setting')
        target, options = value.split(':', 1)
        require(target not in tmpfs, 'unsupported_runtime_setting')
        tmpfs[target] = sorted(options.split(','))
    actual_tmpfs = {target:sorted(options.split(',')) for target,options in (host.get('Tmpfs') or {}).items()}
    require(tmpfs == actual_tmpfs, 'live_tmpfs')
    health = dict(image['Config'].get('Healthcheck') or {})
    health_keys = {'test':'Test','interval':'Interval','timeout':'Timeout','start_period':'StartPeriod','start_interval':'StartInterval','retries':'Retries'}
    rendered_health = service.get('healthcheck') or {}
    require(set(rendered_health) <= set(health_keys), 'unsupported_runtime_setting')
    for key,value in rendered_health.items():
        health[health_keys[key]] = duration_ns(value) if key in ('interval','timeout','start_period','start_interval') else value
    actual_health = live['Config'].get('Healthcheck') or {}
    require(set(health) <= set(health_keys.values()) and set(actual_health) <= set(health_keys.values()), 'live_healthcheck')
    require(all(health.get(k, [] if k == 'Test' else 0) == actual_health.get(k, [] if k == 'Test' else 0)
                for k in health_keys.values()), 'live_healthcheck')
    name = service.get('container_name', config.get('name', PROJECT) + '-' + SERVICE + '-1')
    require(live['Name'].lstrip('/') == name, 'live_container_name')
    endpoints = service.get('networks') or {}
    require(bool(endpoints), 'unsupported_runtime_setting')
    priorities = []
    for key, endpoint in endpoints.items():
        endpoint = endpoint or {}
        require(set(endpoint) <= {'aliases','ipv4_address','ipv6_address','link_local_ips','driver_opts','gw_priority','priority'}, 'unsupported_runtime_setting')
        network_name = config['networks'][key]['name']
        actual = live['NetworkSettings']['Networks'].get(network_name, {})
        ipam = actual.get('IPAMConfig') or {}
        require(set(ipam) <= {'IPv4Address','IPv6Address','LinkLocalIPs'}, 'live_network_endpoint')
        require(ipam.get('IPv4Address', '') == endpoint.get('ipv4_address', '')
                and ipam.get('IPv6Address', '') == endpoint.get('ipv6_address', '')
                and sorted(ipam.get('LinkLocalIPs') or []) == sorted(endpoint.get('link_local_ips') or [])
                and sorted(actual.get('Aliases') or []) == sorted({name, SERVICE, *(endpoint.get('aliases') or [])})
                and (actual.get('DriverOpts') or {}) == (endpoint.get('driver_opts') or {})
                and not actual.get('Links') and actual.get('GwPriority', 0) == endpoint.get('gw_priority', 0), 'live_network_endpoint')
        priorities.append((-endpoint.get('priority', 0), key, network_name))
    require(set(live['NetworkSettings']['Networks']) == {v[2] for v in priorities}, 'live_network_endpoint')
    require(host.get('NetworkMode') == sorted(priorities)[0][2], 'live_network_mode')


def assert_live_contract(config, live, image):
    """Reject stale env-file interpolation or mounted code before comparing candidates."""
    service = config['services'][SERVICE]
    require(not service.get('build'), 'compose_build')
    require(service['image'] == live['Config']['Image'], 'live_image_reference')
    assert_runtime_policy(config, live, image, daemon_logging())
    env = dict(v.split('=', 1) for v in image['Config'].get('Env', []))
    env.update({k: str(v) for k, v in service.get('environment', {}).items()})
    require(env == dict(v.split('=', 1) for v in live['Config'].get('Env', [])), 'live_environment')
    for key, compose_key in [('Cmd', 'command'), ('Entrypoint', 'entrypoint'), ('User', 'user'), ('WorkingDir', 'working_dir')]:
        expected = service.get(compose_key, image['Config'].get(key))
        if compose_key in ('command', 'entrypoint') and compose_key in service:
            # Compose JSON retains escaped dollars; container creation collapses
            # each $$ pair. Image defaults already contain the actual Docker argv.
            if isinstance(expected, list):
                require(all(isinstance(part, str) for part in expected), 'live_' + compose_key)
                expected = [part.replace('$$', '$') for part in expected]
            elif isinstance(expected, str):
                expected = expected.replace('$$', '$')
        actual = live['Config'].get(key)
        require(expected == actual or (expected in (None, '', []) and actual in (None, '', [])), 'live_' + compose_key)
    mounts = []
    for v in service.get('volumes', []):
        require(isinstance(v, dict) and v['type'] in ('volume', 'bind'), 'live_mount_format')
        source = v.get('source')
        if v['type'] == 'volume':
            source = config.get('volumes', {}).get(source, {}).get('name')
            require(bool(source), 'live_mount_name')
        mounts.append((v['type'], source, v['target'], not v.get('read_only', False)))
    actual_mounts = [(v['Type'], v.get('Name') if v['Type'] == 'volume' else v['Source'], v['Destination'], v['RW']) for v in live['Mounts']]
    require(sorted(mounts) == sorted(actual_mounts), 'live_mounts')
    for v in live['Mounts']:
        require(not (v['Destination'].rstrip('/') in ('/app', '/app/backend', '/app/backend/app', '/app/backend/app/static')
                     or v['Destination'].startswith('/app/backend/app/')), 'live_code_bind')
    networks = {config['networks'][name]['name'] for name in service.get('networks', {})}
    require(networks == set(live['NetworkSettings']['Networks']), 'live_networks')


def compose_command(state, override):
    args = ['docker', '--host', 'unix:///var/run/docker.sock', 'compose', '--project-name', state['project'],
            '--project-directory', state['working_dir'], '--env-file', state['env_file']]
    for path in [*state['files'], str(override)]:
        args += ['-f', path]
    return args


def recreate_command(state, override):
    return compose_command(state, override) + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', SERVICE]


def render(state, override):
    # Check local endpoint/root just as docker(); config output is never printed/saved.
    require(os.geteuid() == 0 and not os.environ.get('DOCKER_CONTEXT')
            and os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock') == 'unix:///var/run/docker.sock', 'docker_endpoint')
    return json.loads(command(compose_command(state, override) + ['config', '--format', 'json']))


def override(path, image, release):
    require(bool(REFERENCE.fullmatch(image)) and bool(SHA.fullmatch(release)), 'override')
    save(path, {'services': {SERVICE: {'image': image, 'environment': {'PLATFORM_RELEASE_SHA': release}}}})


def snapshots():
    ids = docker('ps', '-aq', '--no-trunc').decode().split()
    values = json.loads(docker('inspect', *ids)) if ids else []
    result = {}
    for c in values:
        mounts = sorted(c['Mounts'], key=lambda v: (v.get('Destination', ''), v.get('Source', ''), v.get('Type', '')))
        result[c['Name']] = {'id': c['Id'], 'image_id': c['Image'], 'pid': c['State']['Pid'],
                             'started_at': c['State']['StartedAt'], 'restarts': c['RestartCount'],
                             'config': digest(canonical(c['Config'])), 'hostconfig': digest(canonical(c['HostConfig'])),
                             'mounts': digest(canonical(mounts))}
    return result, values


def unchanged_others(before, after, api_name):
    require({k: v for k, v in before.items() if k != api_name} ==
            {k: v for k, v in after.items() if k != api_name}, 'other_container_changed')


def input_hashes(state):
    return {path: digest(regular(path).read_bytes()) for path in [*state['files'], state['env_file']]}


def live_api(values):
    apis = [c for c in values if (c['Config'].get('Labels') or {}).get('com.docker.compose.project') == PROJECT
            and (c['Config'].get('Labels') or {}).get('com.docker.compose.service') == SERVICE]
    require(len(apis) == 1 and apis[0]['State']['Running'], 'live_api')
    return apis[0]


def build(stage_root, output):
    manifest = validate_stage(stage_root)
    base = inspect_image(manifest['base_image_id'])
    require(len(base['RootFS']['Layers']) < 128, 'image_layer_budget')
    manifest_hash = digest(private_read(Path(stage_root)/'manifest.json'))
    tag = application_reference(manifest['release_sha'], manifest_hash)
    require(not docker('image', 'ls', '--quiet', '--no-trunc', '--filter', 'reference=' + tag).strip(), 'release_tag_exists')
    private_dir(output, create=True)
    # No RUN instructions, package install, network or remote base pull.
    log = docker('build', '--network=none', '--pull=false', '--no-cache', '--tag', tag,
                 '--file', str(Path(stage_root)/'Dockerfile'), str(stage_root))
    save(Path(output)/'build.stdout', log)
    derived = json.loads(docker('image', 'inspect', tag))[0]
    resolve_reference(tag, derived['Id'])
    assert_derived_image(base, derived, manifest['release_sha'])
    save(Path(output)/'build.json', {'image_id': derived['Id'], 'image_reference': tag, 'base_image_id': manifest['base_image_id'],
                                   'release_sha': manifest['release_sha'], 'manifest_sha256': manifest_hash})


def plan(stage_root, build_receipt, recipe, output):
    manifest = validate_stage(stage_root)
    built, recipe = read_json(build_receipt), read_json(recipe)
    require(built['base_image_id'] == manifest['base_image_id'] and built['release_sha'] == manifest['release_sha']
            and built['manifest_sha256'] == digest(private_read(Path(stage_root)/'manifest.json'))
            and built['image_reference'] == application_reference(manifest['release_sha'], built['manifest_sha256']), 'build_receipt')
    resolve_reference(built['image_reference'], built['image_id'])
    baseline, values = snapshots(); live = live_api(values)
    readiness = read_json(Path(stage_root)/'readiness.json'); api = api_from_receipt(readiness)
    guardrails(readiness)
    require(live['Id'] == api['id'] and live['Image'] == api['image_id']
            and digest(canonical(live['Config'])) == api['config_sha256']
            and digest(canonical(live['HostConfig'])) == api['hostconfig_sha256'], 'runtime_changed')
    labels = live['Config']['Labels']
    require(recipe['project'] == PROJECT and recipe['files'] == labels['com.docker.compose.project.config_files'].split(',')
            and recipe['working_dir'] == labels['com.docker.compose.project.working_dir']
            and recipe['image_id'] == live['Image'], 'compose_recipe')
    require(recipe['env_file'] == str(PRIVATE/'platform.env'), 'compose_environment_path')
    for item in api['compose_input_files']:
        require(digest(regular(item['path']).read_bytes()) == item['sha256'], 'compose_input_changed')
    old_sha = dict(v.split('=', 1) for v in live['Config']['Env'])['PLATFORM_RELEASE_SHA']
    require(recipe['runtime_release_sha'] == old_sha, 'runtime_release')
    inspect_image(built['image_id'])
    assert_derived_image(inspect_image(live['Image']), inspect_image(built['image_id']), manifest['release_sha'])
    old_reference = live['Config']['Image']
    resolve_reference(old_reference, live['Image'])
    root = private_dir(output, create=True)
    override(root/'compose.rollback.json', old_reference, old_sha)
    override(root/'compose.candidate.json', built['image_reference'], manifest['release_sha'])
    before = render(recipe, root/'compose.rollback.json'); after = render(recipe, root/'compose.candidate.json')
    assert_live_contract(before, live, inspect_image(live['Image']))
    assert_parity(before, after, built['image_reference'], manifest['release_sha'])
    state = {**recipe, 'version': 1, 'old_image': live['Image'], 'old_release': old_sha, 'old_id': live['Id'],
             'new_image': built['image_id'], 'release': manifest['release_sha'], 'api_name': live['Name'],
             'new_image_reference': built['image_reference'], 'old_image_reference': old_reference, 'manifest_sha256': built['manifest_sha256'],
             'old_hostconfig': hostconfig_hash(live['HostConfig']),
             'inputs': input_hashes(recipe), 'before_config': digest(canonical(before)), 'after_config': digest(canonical(after)),
             'baseline_containers': baseline, 'guardrails': readiness, 'owner': str(uuid.uuid4()), 'deployment_id': uuid.uuid4().hex}
    save(root/'state.json', state)


def locks(state, acquire):
    expected = {PRIVATE/'agent-brain-action.lock': {'owner': (state['owner']+'\n').encode()},
                PRIVATE/'deploy-input.lock': {'owner.json': canonical({'deployment_id': state['deployment_id'], 'release_sha': state['release']})+b'\n'}}
    for path, files in expected.items():
        if acquire and not path.exists():
            path.mkdir(mode=0o700)
            for name, raw in files.items(): save(path/name, raw)
        private_dir(path)
        require({p.name for p in path.iterdir()} == set(files), 'deployment_locked')
        for name, raw in files.items(): require(private_read(path/name) == raw, 'deployment_locked')
    return expected


def release_locks(state):
    owned = locks(state, False)
    for path, files in owned.items():
        for name in files: (path/name).unlink()
        path.rmdir()


def transact(root, rollback=False):
    root = private_dir(root); state = read_json(root/'state.json')
    assert_reference_bindings(state)
    require(input_hashes(state) == state['inputs'], 'compose_input_changed')
    guardrails(state['guardrails'])
    before, values = snapshots()
    unchanged_others(state['baseline_containers'], before, state['api_name'])
    if rollback:
        matches = [c for c in values if (c['Config'].get('Labels') or {}).get('com.docker.compose.project') == PROJECT
                   and (c['Config'].get('Labels') or {}).get('com.docker.compose.service') == SERVICE]
        require(len(matches) <= 1, 'rollback_runtime')
        live = matches[0] if matches else None
        if live is not None and live['State']['Running']:
            require(live['Image'] == state['new_image'] and live['Config']['Image'] == state['new_image_reference'], 'rollback_runtime')
        else:
            # Explicit recovery from a failed recreate, never an automatic fallback.
            started = read_json(root/'apply.started.json')
            require(started.get('image_id') == state['new_image'] and started.get('release_sha') == state['release'], 'rollback_attempt')
            require(live is None or live['Image'] in (state['old_image'], state['new_image']), 'rollback_runtime')
            locks(state, False)
    else:
        live = live_api(values)
        require(live['Id'] == state['old_id'] and live['Image'] == state['old_image']
                and live['Config']['Image'] == state['old_image_reference'], 'runtime_changed')
        require(before == state['baseline_containers'], 'runtime_changed')
    old = render(state, root/'compose.rollback.json'); new = render(state, root/'compose.candidate.json')
    require(digest(canonical(old)) == state['before_config'] and digest(canonical(new)) == state['after_config'], 'compose_changed')
    assert_parity(old, new, state['new_image_reference'], state['release'])
    selected, image, release = (root/'compose.rollback.json', state['old_image'], state['old_release']) if rollback else (root/'compose.candidate.json', state['new_image'], state['release'])
    reference = state['old_image_reference'] if rollback else state['new_image_reference']
    # Independent of config-only parity: effective image env and live data paths must still match.
    if live is not None and live['State']['Running']:
        assert_live_contract(new if rollback else old, live, inspect_image(live['Image']))
    inspect_image(image)
    locks(state, True)
    require(input_hashes(state) == state['inputs'], 'compose_input_changed')
    guardrails(state['guardrails'])
    immediate, _ = snapshots()
    require(immediate == before, 'runtime_changed')
    assert_reference_bindings(state)
    action = 'rollback' if rollback else 'apply'
    save(root/(action+'.started.json'), {'action': action, 'time': time.time(), 'image_id': image, 'image_reference': reference, 'release_sha': release})
    # Failure after acquiring locks deliberately retains them; no silent rollback.
    command(recreate_command(state, selected))
    for _ in range(60):
        after, values = snapshots(); current = live_api(values)
        unchanged_others(state['baseline_containers'], after, state['api_name'])
        guardrails(state['guardrails'])
        env = dict(v.split('=', 1) for v in current['Config']['Env'])
        resolve_reference(reference, image)
        require(current['Image'] == image and current['Config']['Image'] == reference
                and env.get('PLATFORM_RELEASE_SHA') == release, 'post_runtime')
        assert_live_contract(old if rollback else new, current, inspect_image(image))
        require(hostconfig_hash(current['HostConfig']) == state['old_hostconfig'], 'api_hostconfig_changed')
        health = current['State'].get('Health', {}).get('Status')
        if health == 'healthy':
            save(root/(action+'.complete.json'), {'action': action, 'image_id': image, 'image_reference': reference, 'release_sha': release,
                                                'api_id': current['Id'], 'other_containers_unchanged': True, 'time': time.time()})
            release_locks(state)
            return
        require(health in ('starting', 'unhealthy'), 'post_health_missing')
        time.sleep(2)
    raise ReleaseError('post_health_timeout')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ReleaseError('arguments')


def main(argv=None):
    os.umask(0o077)
    parser = Parser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    p = subs.add_parser('stage')
    for flag in ['repository', 'baseline', 'release', 'readiness', 'code-baseline', 'static-baseline', 'dist', 'output']: p.add_argument('--'+flag, required=True)
    p = subs.add_parser('build'); p.add_argument('--stage', required=True); p.add_argument('--output', required=True)
    p = subs.add_parser('plan')
    for flag in ['stage', 'build-receipt', 'recipe', 'output']: p.add_argument('--'+flag, required=True)
    for action in ['apply', 'rollback', 'inspect-plan']:
        p = subs.add_parser(action); p.add_argument('--transaction', required=True)
    args = parser.parse_args(argv)
    if args.action == 'stage':
        stage(args.repository, args.baseline, args.release, read_json(args.readiness), read_json(args.code_baseline), args.dist, args.output, read_json(args.static_baseline))
    elif args.action == 'build': build(args.stage, args.output)
    elif args.action == 'plan': plan(args.stage, args.build_receipt, args.recipe, args.output)
    elif args.action == 'inspect-plan':
        root = private_dir(args.transaction); state = read_json(root/'state.json')
        assert_reference_bindings(state)
        require(input_hashes(state) == state['inputs'], 'compose_input_changed')
        old, new = render(state, root/'compose.rollback.json'), render(state, root/'compose.candidate.json')
        require(digest(canonical(old)) == state['before_config'] and digest(canonical(new)) == state['after_config'], 'compose_changed')
        assert_parity(old, new, state['new_image_reference'], state['release'])
    else: transact(args.transaction, rollback=args.action == 'rollback')
    print('SCOPED_API_RELEASE_OK action=' + args.action)


if __name__ == '__main__':
    try:
        main()
    except (ReleaseError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        stage_name = str(error) if isinstance(error, ReleaseError) else 'input_or_operation'
        print('SCOPED_API_RELEASE_FAILED stage=' + stage_name, file=sys.stderr)
        sys.exit(1)
