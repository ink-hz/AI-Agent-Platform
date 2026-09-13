#!/bin/bash
set -euo pipefail
umask 077
deployment_id=11111111111111111111111111111111
action_token=11111111-1111-4111-8111-111111111111
release_sha=ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7
source_tree=a7489e2a43d09ee8727748488abdcbf64f45b9e9
image_id=sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59
manifest_sha=38544645eb7d2604af3dbe2214949099cbf6f6d85df7756203b4c5db27a04d2e
api_id=3347e61ede7d3ab0bd96c0cdc25cff1d6808c89ab2b8a31f24953bebccd4fcf7
knowledge_release=hr-intelligence-57d25a1702f4718737fb7779
knowledge_sha=1156e8e5b75eabd8d342636b3dfac401758f06cfb5c0311bb448a11cde256652
knowledge_bytes=208224
configuration_revision=58697a41d705ad332764f81a57011930e78e1b57462293f6cea036c78e4f3280
input_root=/opt/orbbec-agent-platform/private/hr-provision-inputs/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7-11111111111111111111111111111111
metadata_root=/data/orbbec-agent-platform/release-metadata/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7/hr-provision-11111111111111111111111111111111
release_root=/opt/orbbec-agent-platform/releases/$release_sha
platform_private=/opt/orbbec-agent-platform/private
platform_env=$platform_private/platform.env
private_target=$platform_private/hr-agent
knowledge_root=/data/orbbec-agent-platform/hr-knowledge
knowledge_target=$knowledge_root/releases/$knowledge_release
knowledge_current=$knowledge_root/current.json
work_target=/data/orbbec-agent-platform/hr-work
stage_root=/data/orbbec-agent-platform/provision-staging/$deployment_id
hr_volume=orbbec-agent-platform-hr-agent-secrets
api_volume=orbbec-agent-platform-api-secrets
action_lock=$platform_private/agent-brain-action.lock
action_owner_temp=$platform_private/.hr-provision-action-owner.$deployment_id
release_created=0
releases_parent_created=0
current_created=0
private_created=0
work_created=0
volume_created=0
stage_created=0
action_owned=0
action_created=0
finished=0

release_action_lock() {
  [[ "$action_owned" == 1 ]] || return 0
  [[ -d "$action_lock" && ! -L "$action_lock" ]] || return 1
  [[ -f "$action_lock/owner" && ! -L "$action_lock/owner" ]] || return 1
  [[ "$(/usr/bin/stat -c '%a %U' "$action_lock/owner")" == '600 root' ]] || return 1
  [[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]] || return 1
  [[ "$(/usr/bin/find "$action_lock" -mindepth 1 -maxdepth 1 -printf '%f\n' | /usr/bin/sort)" == owner ]] || return 1
  tombstone=$action_lock.releasing.$action_token
  [[ ! -e "$tombstone" && ! -L "$tombstone" ]] || return 1
  /bin/mv "$action_lock" "$tombstone" || return 1
  /bin/rm "$tombstone/owner" || return 1
  /bin/rmdir "$tombstone" || return 1
  action_owned=0
  action_created=0
}

cleanup() {
  original=$?
  trap - EXIT INT TERM HUP
  set +e
  cleanup_ok=1
  if [[ -f "$action_owner_temp" && ! -L "$action_owner_temp" ]] && \
      [[ "$(/usr/bin/stat -c '%a %U' "$action_owner_temp")" == '600 root' ]] && \
      [[ "$(/bin/cat "$action_owner_temp")" == "$action_token" ]]; then
    /bin/rm "$action_owner_temp" || cleanup_ok=0
  elif [[ -e "$action_owner_temp" || -L "$action_owner_temp" ]]; then
    cleanup_ok=0
  fi
  if [[ "$finished" != 1 ]]; then
    if [[ "$current_created" == 1 ]]; then
      [[ -f "$knowledge_current" && ! -L "$knowledge_current" ]] && \
        [[ "$(/usr/bin/sha256sum "$knowledge_current" | /usr/bin/cut -d' ' -f1)" == c2df4a8fdf5a4b59acdfe4a97a24c3e8be07ff48de6f0c972f87d5473847e467 ]] && \
        /bin/rm "$knowledge_current" || cleanup_ok=0
    fi
    if [[ "$release_created" == 1 ]]; then
      [[ -d "$knowledge_target" && ! -L "$knowledge_target" && -d "$stage_root" ]] && \
        /bin/mv "$knowledge_target" "$stage_root/release-cleanup" && \
        /bin/rm -rf "$stage_root/release-cleanup" || cleanup_ok=0
    fi
    if [[ "$releases_parent_created" == 1 ]]; then /bin/rmdir "$knowledge_root/releases" || cleanup_ok=0; fi
    if [[ "$work_created" == 1 ]]; then /bin/rmdir "$work_target" || cleanup_ok=0; fi
    if [[ "$private_created" == 1 ]]; then
      /bin/rm -f "$private_target/hr-budget-profile.json" "$private_target/hr-content-keyring.json" \
        "$private_target/hr-diagnostic-profile.json" "$private_target/hr-provider-credential" \
        "$private_target/hr-provider-profile.json" "$private_target/hr-release-policy.json" \
        "$private_target/runtime.env" "$private_target/api-runtime.env" "$private_target/worker-runtime.env" || cleanup_ok=0
      /bin/rmdir "$private_target" || cleanup_ok=0
    fi
    if [[ "$volume_created" == 1 ]]; then
      label="$(/usr/bin/docker volume inspect --format '{{index .Labels "hr.provision.deployment"}}' "$hr_volume" 2>/dev/null)"
      [[ "$label" == "$deployment_id" ]] && /usr/bin/docker volume rm "$hr_volume" >/dev/null || cleanup_ok=0
    fi
  fi
  if [[ "$stage_created" == 1 && -d "$stage_root" && ! -L "$stage_root" ]]; then /bin/rm -rf "$stage_root" || cleanup_ok=0; fi
  if [[ "$action_created" == 1 && "$action_owned" == 0 ]]; then
    if [[ -d "$action_lock" && ! -L "$action_lock" ]] && [[ -z "$(/usr/bin/find "$action_lock" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      /bin/rmdir "$action_lock" || cleanup_ok=0
      action_created=0
    elif [[ -f "$action_lock/owner" && ! -L "$action_lock/owner" ]] && [[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]]; then
      action_owned=1
    else
      cleanup_ok=0
    fi
  fi
  if ! release_action_lock; then cleanup_ok=0; fi
  if [[ -d "$input_root" && ! -L "$input_root" ]]; then /bin/rm -rf "$input_root" || cleanup_ok=0; fi
  if [[ "$cleanup_ok" != 1 ]]; then original=1; fi
  /usr/bin/printf '%s\n' "$original" > "$metadata_root/exit_code.part"
  /bin/chmod 600 "$metadata_root/exit_code.part"
  /bin/mv "$metadata_root/exit_code.part" "$metadata_root/exit_code"
  exit "$original"
}
on_signal() { trap - INT TERM HUP; exit "$((128 + $1))"; }
trap cleanup EXIT
trap 'on_signal 2' INT
trap 'on_signal 15' TERM
trap 'on_signal 1' HUP

[[ "$input_root" =~ ^/opt/orbbec-agent-platform/private/hr-provision-inputs/$release_sha-[0-9a-f]{32}$ ]]
[[ "$metadata_root" =~ ^/data/orbbec-agent-platform/release-metadata/$release_sha/hr-provision-[0-9a-f]{32}$ ]]
[[ "$(/usr/bin/id -u)" == 0 ]]
[[ -d "$release_root" && ! -L "$release_root" ]]
[[ "$(/usr/bin/docker image inspect --format '{{.Id}}' "$image_id")" == "$image_id" ]]
[[ "$(/usr/bin/docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image_id" | /usr/bin/sed -n 's/^PLATFORM_RELEASE_SHA=//p')" == "$release_sha" ]]
[[ "$(/usr/bin/sha256sum "$release_root/MANIFEST.sha256" | /usr/bin/cut -d' ' -f1)" == "$manifest_sha" ]]
[[ "$(/usr/bin/docker inspect --format '{{.Id}}' orbbec-agent-platform-platform-api-1)" == "$api_id" ]]
[[ "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$api_id")" == true ]]
[[ "$(/usr/bin/docker inspect --format '{{range .Mounts}}{{if eq .Destination "/run/secrets"}}{{.Name}}{{end}}{{end}}' "$api_id")" == "$api_volume" ]]
[[ -d "$knowledge_root" && ! -L "$knowledge_root" ]]
[[ "$(/usr/bin/stat -c '%a %U:%G' "$platform_private")" == '700 root:root' ]]
[[ -f "$platform_env" && ! -L "$platform_env" && "$(/usr/bin/stat -c '%a %U:%G' "$platform_env")" == '600 root:root' ]]
[[ ! -e "$private_target" && ! -L "$private_target" ]]
[[ ! -e "$work_target" && ! -L "$work_target" ]]
[[ ! -e "$knowledge_current" && ! -L "$knowledge_current" ]]
[[ ! -e "$knowledge_target" && ! -L "$knowledge_target" ]]
! /usr/bin/docker volume inspect "$hr_volume" >/dev/null 2>&1

/usr/bin/printf '%s\n' "$action_token" > "$action_owner_temp"
/bin/chmod 600 "$action_owner_temp"
/bin/mkdir "$action_lock"
/bin/chmod 700 "$action_lock"
action_created=1
/bin/mv "$action_owner_temp" "$action_lock/owner"
action_owned=1

staging_parent=/data/orbbec-agent-platform/provision-staging
if [[ ! -e "$staging_parent" && ! -L "$staging_parent" ]]; then /usr/bin/install -d -o root -g root -m 700 "$staging_parent"; fi
[[ -d "$staging_parent" && ! -L "$staging_parent" && "$(/usr/bin/stat -c '%a %U:%G' "$staging_parent")" == '700 root:root' ]]
/bin/mkdir "$stage_root"
/bin/chmod 700 "$stage_root"
stage_created=1

python3 - "$knowledge_root" "$metadata_root/knowledge-before.json" <<'PY'
import hashlib, json, os, pathlib, stat, sys
root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
def snapshot(root):
    result = {}
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in sorted(dirs + files):
            path = pathlib.Path(base) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise SystemExit('knowledge_tree_type_invalid')
            item = {'type': 'directory' if stat.S_ISDIR(info.st_mode) else 'regular',
                    'uid': info.st_uid, 'gid': info.st_gid,
                    'mode': stat.S_IMODE(info.st_mode), 'size': info.st_size}
            if stat.S_ISREG(info.st_mode):
                item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[relative] = item
    return result
payload = {'schema_version': 1, 'items': snapshot(root)}
with output.open('x') as stream:
    json.dump(payload, stream, sort_keys=True, separators=(',', ':'))
    stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
os.chmod(output, 0o600)
PY

/bin/mkdir "$stage_root/secrets" "$stage_root/knowledge" "$stage_root/env" "$stage_root/work"
for name in hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json; do
  /usr/bin/install -o 10001 -g 10001 -m 600 "$input_root/$name" "$stage_root/secrets/$name"
done
/bin/chown 10001:10001 "$stage_root/secrets" "$stage_root/knowledge" "$stage_root/env" "$stage_root/work"
/bin/chmod 700 "$stage_root/secrets" "$stage_root/knowledge" "$stage_root/env" "$stage_root/work"

python3 - "$input_root/knowledge.tar.gz" "$stage_root/knowledge" "$knowledge_release" <<'PY'
import json, pathlib, stat, sys, tarfile
archive_path, destination, release = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
seen = set()
with tarfile.open(archive_path, 'r:gz') as archive:
    for member in archive.getmembers():
        parts = pathlib.PurePosixPath(member.name).parts
        if (not member.name or pathlib.PurePosixPath(member.name).is_absolute()
                or any(part in {'', '.', '..'} for part in parts)
                or member.name in seen or not (member.isdir() or member.isreg())):
            raise SystemExit('knowledge_archive_invalid')
        seen.add(member.name)
        target = destination.joinpath(*parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None: raise SystemExit('knowledge_archive_invalid')
            with target.open('xb') as output:
                output.write(source.read())
if json.loads((destination / 'current.json').read_text()) != {'release_id': release}:
    raise SystemExit('knowledge_pointer_invalid')
expected = destination / 'releases' / release
if not expected.is_dir(): raise SystemExit('knowledge_release_missing')
PY
/bin/chown -R 10001:10001 "$stage_root/knowledge"
/usr/bin/find "$stage_root/knowledge" -type d -exec /bin/chmod 755 {} +
/usr/bin/find "$stage_root/knowledge" -type f -exec /bin/chmod 644 {} +

python3 - "$platform_env" "$stage_root/runtime.env" "$stage_root/secrets/hr-provider-profile.json" "$image_id" <<'PY'
import json, os, pathlib, re, sys
source, target, provider_path, image = map(pathlib.Path, sys.argv[1:])
provider = json.loads(provider_path.read_text())
model = provider.get('model')
if not isinstance(model, str) or re.fullmatch(r'[A-Za-z0-9._:/-]{1,128}', model) is None:
    raise SystemExit('provider_model_invalid')
payload = source.read_bytes()
if b'\x00' in payload: raise SystemExit('platform_env_invalid')
if payload and not payload.endswith(b'\n'): payload += b'\n'
payload += ('PLATFORM_IMAGE=' + str(image) + '\n'
            + 'PLATFORM_HR_AGENT_MODEL=' + model + '\n'
            + 'PLATFORM_HR_AGENT_ATTACHMENTS_ENABLED=1\n'
            + 'PLATFORM_HR_WEB_WORKER_ENABLED=1\n').encode()
with target.open('xb') as stream:
    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
os.chown(target, 10001, 10001); os.chmod(target, 0o600)
PY

compose_json=$stage_root/compose.json
/usr/bin/docker compose --project-name orbbec-agent-platform \
  --env-file "$stage_root/runtime.env" \
  -f "$release_root/deploy/cloud/compose.yaml" \
  -f "$release_root/deploy/cloud/compose.hr-agent.yaml" \
  --profile hr-agent config --format json > "$compose_json"
/bin/chmod 600 "$compose_json"

python3 - "$compose_json" "$stage_root/env" "$image_id" <<'PY'
import json, os, pathlib, sys
document = json.loads(pathlib.Path(sys.argv[1]).read_text())
output = pathlib.Path(sys.argv[2]); image = sys.argv[3]
services = document.get('services')
if not isinstance(services, dict): raise SystemExit('compose_services_invalid')
for service, filename in (('platform-api', 'api-runtime.env'), ('platform-hr-agent-worker', 'worker-runtime.env')):
    value = services.get(service)
    if not isinstance(value, dict) or value.get('image') != image:
        raise SystemExit('compose_image_invalid')
    if value.get('read_only') is not True or str(value.get('user')) != '10001:10001':
        raise SystemExit('compose_confinement_invalid')
    environment = value.get('environment')
    if not isinstance(environment, dict): raise SystemExit('compose_environment_invalid')
    lines = []
    for key in sorted(environment):
        item = environment[key]
        if item is None: item = ''
        item = str(item)
        if '\n' in item or '\r' in item or '\x00' in item: raise SystemExit('compose_environment_invalid')
        lines.append(f'{key}={item}')
    path = output / filename
    with path.open('x') as stream:
        stream.write('\n'.join(lines) + '\n'); stream.flush(); os.fsync(stream.fileno())
    os.chown(path, 10001, 10001); os.chmod(path, 0o600)
PY
/bin/rm "$compose_json"

/usr/bin/docker volume create --label "hr.provision.deployment=$deployment_id" "$hr_volume" >/dev/null
volume_created=1
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL --cap-add CHOWN \
  --security-opt no-new-privileges:true --user 0:0 -v "$hr_volume:/out" "$image_id" \
  /bin/sh -ec '/bin/chmod 700 /out && /bin/chown 10001:10001 /out'
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 10001:10001 \
  -v "$stage_root/secrets:/input:ro" -v "$api_volume:/api:ro" -v "$hr_volume:/out" \
  "$image_id" /bin/sh -ec '
set -eu; umask 077
for name in hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json; do
  test -f "/input/$name" && test ! -L "/input/$name"; /bin/cp "/input/$name" "/out/$name"
done
for name in control-database-url attachment-s3-access-key attachment-s3-secret-key content-encryption-keyring; do
  test -f "/api/$name" && test ! -L "/api/$name" && test -s "/api/$name"; /bin/cp "/api/$name" "/out/$name"
done
/usr/bin/test "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f | /usr/bin/wc -l)" -eq 10
/usr/bin/test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 ! -type f -print -quit)"
/bin/chmod 600 /out/*
'
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 10001:10001 -v "$hr_volume:/out:ro" \
  "$image_id" /bin/sh -ec '
set -eu
expected="attachment-s3-access-key attachment-s3-secret-key content-encryption-keyring control-database-url hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json"
actual="$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f -printf "%f\n" | /usr/bin/sort | /usr/bin/tr "\n" " " | /usr/bin/sed "s/ $//")"
test "$actual" = "$expected"
test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 ! -type f -print -quit)"
test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f ! -user 10001 -print -quit)"
test -z "$(/usr/bin/find /out -mindepth 1 -maxdepth 1 -type f ! -perm 600 -print -quit)"
'

preflight_report=$stage_root/preflight.json
set +e
/usr/bin/docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 10001:10001 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700 \
  -v "$api_volume:/run/secrets:ro" -v "$hr_volume:/run/hr-agent-secrets:ro" \
  -v "$stage_root/knowledge:/data/hr-knowledge:ro" -v "$stage_root/work:/data/hr-work" \
  -v "$stage_root/env:/run/preflight-env:ro" \
  "$image_id" python tools/hr_agent/preflight.py --scope public-only \
  --api-env-file /run/preflight-env/api-runtime.env \
  --worker-env-file /run/preflight-env/worker-runtime.env > "$preflight_report"
preflight_status=$?
set -e
[[ "$preflight_status" == 1 ]]
python3 - "$preflight_report" "$configuration_revision" "$knowledge_release" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
if report.get('ok') is not False or report.get('blockers') != ['database_not_checked']:
    raise SystemExit('preflight_blockers_invalid')
for name in ('api', 'worker'):
    item = report.get(name)
    if not isinstance(item, dict) or item.get('ready') is not True:
        raise SystemExit('preflight_configuration_invalid')
if report['api'].get('configuration_fingerprint') != sys.argv[2]:
    raise SystemExit('preflight_revision_invalid')
if report['api'].get('knowledge', {}).get('release_id') != sys.argv[3]:
    raise SystemExit('preflight_knowledge_invalid')
PY
/bin/rm "$preflight_report"

/usr/bin/install -d -o root -g root -m 700 "$private_target"
private_created=1
for name in hr-budget-profile.json hr-content-keyring.json hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json hr-release-policy.json; do
  /usr/bin/install -o root -g root -m 600 "$stage_root/secrets/$name" "$private_target/$name"
done
for name in runtime.env api-runtime.env worker-runtime.env; do
  source=$stage_root/$name
  [[ "$name" == runtime.env ]] || source=$stage_root/env/$name
  /usr/bin/install -o root -g root -m 600 "$source" "$private_target/$name"
done

/usr/bin/install -d -o 10001 -g 10001 -m 700 "$work_target"
work_created=1
if [[ ! -e "$knowledge_root/releases" && ! -L "$knowledge_root/releases" ]]; then
  /usr/bin/install -d -o root -g root -m 755 "$knowledge_root/releases"
  releases_parent_created=1
fi
[[ -d "$knowledge_root/releases" && ! -L "$knowledge_root/releases" ]]
/bin/mv "$stage_root/knowledge/releases/$knowledge_release" "$knowledge_target"
release_created=1
/usr/bin/install -o 10001 -g 10001 -m 644 "$stage_root/knowledge/current.json" "$knowledge_root/.current.$deployment_id"
[[ ! -e "$knowledge_current" && ! -L "$knowledge_current" ]]
/bin/mv "$knowledge_root/.current.$deployment_id" "$knowledge_current"
current_created=1

python3 - "$knowledge_root" "$metadata_root/knowledge-before.json" "$metadata_root/result.json" \
  "$release_sha" "$image_id" "$configuration_revision" "$knowledge_release" <<'PY'
import hashlib, json, os, pathlib, stat, sys
root = pathlib.Path(sys.argv[1]); before = json.load(open(sys.argv[2]))['items']
def snapshot(root):
    result = {}
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in sorted(dirs + files):
            path = pathlib.Path(base) / name; info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise SystemExit('knowledge_tree_type_invalid')
            item = {'type': 'directory' if stat.S_ISDIR(info.st_mode) else 'regular',
                    'uid': info.st_uid, 'gid': info.st_gid,
                    'mode': stat.S_IMODE(info.st_mode), 'size': info.st_size}
            if stat.S_ISREG(info.st_mode): item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[relative] = item
    return result
knowledge_after = snapshot(root)
for name, value in before.items():
    if knowledge_after.get(name) != value: raise SystemExit('old_knowledge_changed')
allowed = {'current.json', 'releases'}
allowed.update(name for name in knowledge_after if name.startswith('releases/' + sys.argv[7]))
if set(knowledge_after) - set(before) - allowed: raise SystemExit('unexpected_knowledge_added')
result = {'schema_version': 1, 'status': 'completed', 'release_sha': sys.argv[4],
          'image_id': sys.argv[5], 'configuration_revision': sys.argv[6],
          'knowledge_release': sys.argv[7], 'knowledge_preserved': True,
          'services_changed': False, 'migrations_run': False,
          'preflight_blockers': ['database_not_checked'],
          'secret_values_logged': False, 'credential_fingerprint_logged': False}
output = pathlib.Path(sys.argv[3])
with output.open('x') as stream:
    json.dump(result, stream, sort_keys=True, separators=(',', ':'))
    stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
os.chmod(output, 0o600)
PY
finished=1
/usr/bin/printf 'HR_PROVISION_OK release=%s image=%s services_changed=false migrations_run=false\n' "$release_sha" "$image_id"
