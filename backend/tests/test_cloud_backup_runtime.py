"""Runtime metadata and pipeline boundary tests; no Docker or production access."""
from copy import deepcopy
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import time
import fcntl
import hashlib

import pytest


ROOT = Path(__file__).parents[2]
HELPER = ROOT / "deploy/cloud/backup_runtime.py"


def runtime_module():
    assert HELPER.is_file(), "runtime-bound backup helper is missing"
    spec = importlib.util.spec_from_file_location("cloud_backup_runtime", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_runtime(tmp_path):
    module = runtime_module()
    private, backups, attachments, postgres = [tmp_path / name for name in (
        "private", "backups", "attachments", "postgres",
    )]
    for directory in (private, backups, attachments, postgres):
        directory.mkdir(mode=0o700)
    secret_dir = tmp_path / 'pg-secrets'; secret_dir.mkdir(mode=0o700)
    module.POSTGRES_SECRET_PATH = secret_dir / 'postgres-owner-password'
    module.POSTGRES_SECRET_PATH.write_bytes(b's' * 64 + b'\n')
    module.POSTGRES_SECRET_PATH.chmod(0o400)
    # Synthetic ownership for local fixtures only; production constants stay 999.
    module.POSTGRES_SECRET_UID, module.POSTGRES_SECRET_GID = os.geteuid(), os.getegid()
    (private / "backup-recovery-x25519.pub").write_bytes(b"p" * 32)
    (private / "backup-recovery-x25519.pub").chmod(0o600)
    configs = [tmp_path / name for name in ("base.yaml", "one.json", "two.json")]
    for index, path in enumerate(configs):
        path.write_text(f"synthetic-input-{index}")
    paths = module.BackupPaths(private, backups, attachments, postgres)
    runner = FakeRunner(paths, configs)
    runtime = module.RuntimeBackup(paths, runner=runner,
                                   clock=lambda: datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    return module, runtime, runner, configs


def container_command(runner, role):
    return next(command for command, _ in runner.commands
                if command[1] == 'create' and 'io.orbbec.backup.role=' + role in command)


class FakeRunner:
    def __init__(self, paths, configs):
        self.paths, self.configs = paths, configs
        self.commands, self.pipelines = [], []
        self.after_pipeline = None
        self.retention_fails = False
        self.producer_fails = False
        self.encryptor_fails = False
        self.containers = {}
        self.jobs = {}
        for index, service in enumerate(("platform-api", "platform-postgres", "platform-minio")):
            self.containers[service] = {
                "id": str(index + 1) * 64, "name": f"/orbbec-agent-platform-{service}-1",
                "image_id": "sha256:" + str(index + 4) * 64,
                "image_reference": ("orbbec-agent-platform:manufacturing-v2-74c7e6b4-c4773a0d"
                                    if service == "platform-api" else f"{service}:stable"),
                "running": True, "started_at": "2026-10-29T10:00:00Z",
                "labels": {
                    "com.docker.compose.project": "orbbec-agent-platform",
                    "com.docker.compose.service": service,
                    "com.docker.compose.oneoff": "False",
                    "com.docker.compose.project.config_files": ",".join(map(str, configs)),
                    "com.docker.compose.project.working_dir": str(configs[0].parent),
                },
                "mounts": [], "networks": {"orbbec-agent-platform-internal": {"IPAddress":"172.30.0.2"}},
            }
        self.containers["platform-postgres"]["mounts"] = [{
            "Type": "bind", "Source": str(paths.postgres_dir),
            "Destination": "/var/lib/postgresql/data", "RW": True,
        }, {"Type":"volume", "Name":"orbbec-agent-platform-postgres-secrets",
            "Source":str(sys.modules['cloud_backup_runtime'].POSTGRES_SECRET_PATH.parent),
            "Destination":"/run/secrets", "RW":False}]
        self.containers["platform-minio"]["mounts"] = [{
            "Type": "bind", "Source": str(paths.attachment_dir),
            "Destination": "/data", "RW": True,
        }]

    def capture(self, command, *, stage, timeout=60):
        self.commands.append((tuple(command), stage))
        args = list(command[1:])
        if args[0] == "ps":
            filters = [args[index + 1] for index, value in enumerate(args[:-1]) if value == '--filter']
            attempt = next((value.split('=', 2)[-1] for value in filters
                            if value.startswith('label=io.orbbec.backup.attempt=')), None)
            if attempt:
                return ('\n'.join(identity for identity, job in self.jobs.items()
                                  if job['labels']['io.orbbec.backup.attempt'] == attempt)).encode()
            service = next(item.split("=", 2)[-1] for item in args
                           if item.startswith("label=com.docker.compose.service="))
            return (self.containers[service]["id"] + "\n").encode()
        if args[0] == "inspect":
            if args[-1] in self.jobs:
                return json.dumps(self.jobs[args[-1]]).encode()
            return json.dumps(next(value for value in self.containers.values()
                                   if value["id"] == args[-1])).encode()
        if args[:2] == ["image", "inspect"]:
            return json.dumps({"id": args[-1], "tags": [
                "orbbec-agent-platform:manufacturing-v2-74c7e6b4-c4773a0d"
            ]}).encode()
        if args[:2] == ["volume", "inspect"]:
            name = args[-1]
            return json.dumps({"name": name, "driver": "local", "mountpoint":str(sys.modules['cloud_backup_runtime'].POSTGRES_SECRET_PATH.parent), "options": (
                {"type": "none", "o": "bind", "device": str(self.paths.backup_dir)}
                if name.endswith("-backups") else {}
            )}).encode()
        if args[:2] == ["volume", "create"]:
            return (args[-1] + "\n").encode()
        if args[0] == 'create':
            name = args[args.index('--name') + 1]
            labels = {args[index + 1].split('=', 1)[0]:args[index + 1].split('=', 1)[1]
                      for index, value in enumerate(args[:-1]) if value == '--label'}
            identity = hashlib.sha256(name.encode()).hexdigest()
            self.jobs[identity] = {'id':identity, 'name':'/' + name, 'labels':labels,
                                   'image_id':next(value for value in args if value.startswith('sha256:')),
                                   'running':False, 'status':'created', 'exit_code':0, 'arguments':list(command)}
            return identity.encode()
        if args[0] == 'start':
            job = self.jobs[args[-1]]
            job.update(running=False, status='exited', exit_code=0)
            if job['labels']['io.orbbec.backup.role'] == 'retention':
                if self.retention_fails:
                    job['exit_code'] = 7
                    raise sys.modules["cloud_backup_runtime"].BackupError("retention")
                return b'{"status":"completed","dry_run":false,"session_count":1,"agent_count":0}'
            return b""
        if args[0] == 'stop':
            self.jobs[args[-1]].update(running=False, status='exited', exit_code=143)
            return b""
        if args[0] == 'rm':
            del self.jobs[args[-1]]
            return b''
        raise AssertionError((args, stage))

    def pipeline(self, producer, encryptor, *, stage, timeout=1800):
        source_job = self.jobs.get(producer[-1]) if producer[1] == 'start' else None
        target_job = self.jobs[encryptor[-1]]
        if source_job:
            source_job.update(running=False, status='exited', exit_code=0)
            producer = source_job['arguments']
        target_job.update(running=False, status='exited', exit_code=0)
        encryptor = target_job['arguments']
        self.pipelines.append((tuple(producer), tuple(encryptor), stage))
        module = sys.modules["cloud_backup_runtime"]
        if self.producer_fails:
            if source_job: source_job['exit_code'] = 8
            raise module.BackupError(stage + "_producer")
        if self.encryptor_fails:
            target_job['exit_code'] = 9
            raise module.BackupError(stage + "_encryptor")
        setting = next(item for item in encryptor if item.startswith("PLATFORM_REPLICA_BACKUP_PATH="))
        target = self.paths.backup_dir / Path(setting.split("=", 1)[1]).name
        target.write_bytes(b"synthetic-ciphertext")
        target.chmod(0o600)
        if self.after_pipeline:
            self.after_pipeline()
        return b'{"status":"backed_up","encrypted_size":20,"plaintext_size":12}'


def test_runtime_uses_running_image_id_with_non_sha_tag_and_complete_ordered_config_identity(tmp_path):
    _, runtime, runner, configs = fixture_runtime(tmp_path)
    # More than one release overlay is significant, including all 43 deployed inputs.
    for index in range(40):
        extra = tmp_path / f"overlay-{index}.json"
        extra.write_text("{}")
        configs.append(extra)
    runner.containers["platform-api"]["labels"]["com.docker.compose.project.config_files"] = ",".join(map(str, configs))
    manifest = runtime.run()
    assert manifest["status"] == "complete"
    assert [item["path"] for item in manifest["runtime"]["compose_files"]] == list(map(str, configs))
    assert all(len(item["sha256"]) == 64 for item in manifest["runtime"]["compose_files"])
    assert manifest["snapshot_consistency"] == "individual_captures_not_cross_resource_atomic"
    assert [item["scope"] for item in manifest["artifacts"]] == [
        "database:agent_platform", "database:agent_platform_control", "minio_data_directory",
    ]
    for producer, encryptor, _ in runner.pipelines:
        assert "sha256:" + "4" * 64 in encryptor
        assert "--pull=never" in encryptor
        assert "--network" in encryptor and "none" in encryptor
        assert "--read-only" in encryptor and "ALL" in encryptor
        assert "compose" not in producer
    assert 'sha256:' + '5' * 64 in runner.pipelines[0][0]
    assert '2' * 64 not in runner.pipelines[0][0]  # Never exec into the shared PG service.
    assert (runtime.paths.private_dir / "last-backup-success").read_text().split() == [
        item["filename"] for item in manifest["artifacts"]
    ]
    assert manifest["retention"]["status"] == "completed"
    assert all(item["capture_started_at"] <= item["capture_finished_at"] for item in manifest["artifacts"])
    assert not any("up" in command for command, _ in runner.commands)


@pytest.mark.parametrize("change", ["project", "service", "mount", "backup_volume", "image"])
def test_runtime_rejects_unknown_resource_without_starting_capture(tmp_path, change):
    module, runtime, runner, _ = fixture_runtime(tmp_path)
    if change in {"project", "service"}:
        runner.containers["platform-api"]["labels"]["com.docker.compose." + change] = "other"
    elif change == "mount":
        runner.containers["platform-minio"]["mounts"][0]["Source"] = "/unexpected"
    elif change == "backup_volume":
        runner.paths = module.BackupPaths(runner.paths.private_dir, tmp_path / "other",
                                         runner.paths.attachment_dir, runner.paths.postgres_dir)
    else:
        runner.containers["platform-api"]["image_reference"] = "untrusted:latest"
    with pytest.raises(module.BackupError):
        runtime.run()
    assert not runner.pipelines
    assert not (runtime.paths.private_dir / "last-backup-success").exists()


@pytest.mark.parametrize("change", ["image", "container", "config", "config_order"])
def test_deployment_change_stops_after_first_capture_preserving_previous_marker(tmp_path, change):
    module, runtime, runner, configs = fixture_runtime(tmp_path)
    marker = runtime.paths.private_dir / "last-backup-success"
    marker.write_text("previous valid backup\n")

    def mutate():
        if change == "image":
            runner.containers["platform-api"]["image_id"] = "sha256:" + "f" * 64
        elif change == "container":
            runner.containers["platform-postgres"]["id"] = "e" * 64
        elif change == "config":
            configs[-1].write_text("changed deployment")
        else:
            label = runner.containers["platform-api"]["labels"]
            label["com.docker.compose.project.config_files"] = ",".join(map(str, reversed(configs)))
    runner.after_pipeline = mutate
    with pytest.raises(module.BackupError, match="runtime_changed"):
        runtime.run()
    assert len(runner.pipelines) == 1
    assert marker.read_text() == "previous valid backup\n"


@pytest.mark.parametrize("side", ["producer", "encryptor"])
def test_incomplete_pipeline_never_advances_marker_or_commits_complete_manifest(tmp_path, side):
    module, runtime, runner, _ = fixture_runtime(tmp_path)
    setattr(runner, side + "_fails", True)
    marker = runtime.paths.private_dir / "last-backup-success"
    marker.write_text("previous\n")
    with pytest.raises(module.BackupError, match=side):
        runtime.run()
    assert marker.read_text() == "previous\n"
    manifests = list((runtime.paths.private_dir / "backup-manifests").glob("*.json"))
    assert not any(json.loads(path.read_text())["status"] == "complete" for path in manifests)


def test_retention_failure_preserves_complete_backup_and_records_separate_failure(tmp_path):
    module, runtime, runner, _ = fixture_runtime(tmp_path)
    runner.retention_fails = True
    with pytest.raises(module.BackupError, match="retention"):
        runtime.run()
    assert (runtime.paths.private_dir / "last-backup-success").exists()
    receipt = json.loads((runtime.paths.private_dir / "last-backup-retention.json").read_text())
    assert receipt["status"] == "failed" and receipt["backup_status"] == "complete"
    complete = runtime.paths.private_dir / "backup-manifests" / (receipt["set_id"] + ".json")
    assert json.loads(complete.read_text())["status"] == "complete"


@pytest.mark.parametrize("producer_rc,encryptor_rc,failed_side", [(7, 0, "producer"), (0, 8, "encryptor")])
def test_real_subprocess_pipeline_checks_both_returns_and_hides_stderr(producer_rc, encryptor_rc, failed_side):
    module = runtime_module()
    producer = [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'synthetic-input'); sys.stderr.write('do-not-log-secret'); sys.exit({producer_rc})"]
    encryptor = [sys.executable, "-c", f"import sys; sys.stdin.buffer.read(); print('{{}}'); sys.stderr.write('do-not-log-secret'); sys.exit({encryptor_rc})"]
    with pytest.raises(module.BackupError, match="capture_" + failed_side) as error:
        module.ProcessRunner().pipeline(producer, encryptor, stage="capture")
    assert "do-not-log-secret" not in str(error.value)
    assert error.value.producer_returncode == producer_rc
    assert error.value.encryptor_returncode == encryptor_rc


def test_real_pipeline_uses_original_cli_ciphertext_and_local_key_can_restore(tmp_path, monkeypatch):
    import io
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    from app.cloud_replica.backup import decrypt_stream

    module = runtime_module()
    key = X25519PrivateKey.generate()
    public = tmp_path / "recovery-public"
    public.write_bytes(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    public.chmod(0o600)
    archive = tmp_path / "synthetic.orb"
    monkeypatch.setenv("PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("PLATFORM_REPLICA_BACKUP_PATH", str(archive))
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "backend"))
    producer = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'synthetic-recovery-canary' * 1000)"]
    encryptor = [sys.executable, "-m", "app.cloud_replica.cli", "backup"]
    output = module.ProcessRunner().pipeline(producer, encryptor, stage="capture")
    assert json.loads(output)["status"] == "backed_up"
    assert b"synthetic-recovery-canary" not in archive.read_bytes()
    restored = io.BytesIO()
    with archive.open("rb") as stream:
        decrypt_stream(stream, restored, key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    assert restored.getvalue() == b"synthetic-recovery-canary" * 1000
    assert sorted(path.name for path in tmp_path.iterdir()) == ["recovery-public", "synthetic.orb"]


def test_shell_wrapper_is_location_relative_and_reports_only_safe_failure_stages():
    module = runtime_module()
    script = (ROOT / "deploy/cloud/backup.sh").read_text()
    assert "backup_runtime.py" in script
    assert "/current/" not in script and "PLATFORM_IMAGE" not in script
    assert "password" not in str(module.BackupError("capture_producer"))
    assert module.BackupError("capture_producer").stage == "capture_producer"


def test_duplicate_run_reports_busy_before_starting_another_set(tmp_path):
    module, runtime, runner, _ = fixture_runtime(tmp_path)
    busy = []

    def overlapping_run():
        runner.after_pipeline = None
        with pytest.raises(module.BackupError, match="lock"):
            runtime.run()
        busy.append(True)
    runner.after_pipeline = overlapping_run
    runtime.run()
    assert busy == [True]
    assert len(runner.pipelines) == 3


def test_public_key_copy_is_private_and_never_mounts_the_entire_private_directory(tmp_path):
    _, runtime, runner, _ = fixture_runtime(tmp_path)
    runtime.run()
    command = container_command(runner, 'public-copy')
    assert str(runtime.paths.private_dir) + ":/source:ro" not in command
    assert str(runtime.paths.private_dir / "backup-recovery-x25519.pub") + ":/source/recovery-public-key:ro" in command
    assert "CHOWN" in command and "ALL" in command and "none" in command
    assert "os.fchown(stream.fileno(), 10001, 10001)" in command[-1]
    assert "os.fchmod(descriptor, 0o600)" in command[-1]


def test_freshness_check_is_read_only_and_validates_manifest_and_ciphertext(tmp_path):
    _, runtime, runner, _ = fixture_runtime(tmp_path)
    manifest = runtime.run()
    runner.commands.clear()
    result = runtime.check(max_age_seconds=26 * 3600)
    assert result["status"] == "fresh"
    assert result["set_id"] == manifest["set_id"]
    assert result["retention_status"] == "completed"
    assert result["max_age_seconds"] == 26 * 3600
    assert runner.commands == []


@pytest.mark.parametrize("change,stage", [
    ("missing_marker", "check_marker"), ("missing_manifest", "check_manifest"),
    ("bad_manifest", "check_manifest"), ("corrupt_artifact", "check_artifact"),
    ("stale", "check_stale"),
])
def test_freshness_check_rejects_missing_bad_or_stale_evidence(tmp_path, change, stage):
    module, runtime, _, _ = fixture_runtime(tmp_path)
    manifest = runtime.run()
    path = runtime.paths.private_dir / "backup-manifests" / (manifest["set_id"] + ".json")
    if change == "missing_marker":
        (runtime.paths.private_dir / "last-backup-success").unlink()
    elif change == "missing_manifest":
        path.unlink()
    elif change == "bad_manifest":
        value = json.loads(path.read_text())
        value["artifacts"][0]["filename"] = "../../other"
        path.write_text(json.dumps(value))
    elif change == "corrupt_artifact":
        (runtime.paths.backup_dir / manifest["artifacts"][0]["filename"]).write_bytes(b"x" * 20)
    else:
        runtime.clock = lambda: datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    with pytest.raises(module.BackupError, match=stage):
        runtime.check(max_age_seconds=26 * 3600)


@pytest.mark.parametrize("failed_file,expected_stage", [
    ("manifest", "manifest"), ("last-backup-success", "marker"),
    ("last-backup-retention.json", "retention_receipt"),
])
def test_receipt_write_failure_preserves_primary_stage_and_previous_success(tmp_path, monkeypatch, failed_file, expected_stage):
    module, runtime, _, _ = fixture_runtime(tmp_path)
    marker = runtime.paths.private_dir / "last-backup-success"
    marker.write_text("previous complete set\n")
    original = module.atomic_write

    def unavailable(path, value):
        if ((failed_file == "manifest" and path.parent.name == "backup-manifests"
             and not path.name.endswith(".retention.json")) or path.name == failed_file
                or path.name == "last-backup-failure.json"):
            raise OSError("sensitive-disk-details")
        original(path, value)
    monkeypatch.setattr(module, "atomic_write", unavailable)
    with pytest.raises(module.BackupError, match=expected_stage) as error:
        runtime.run()
    assert "sensitive-disk-details" not in str(error.value)
    if failed_file != "last-backup-retention.json":
        assert marker.read_text() == "previous complete set\n"
    else:
        assert marker.read_text() != "previous complete set\n"


def test_archive_directory_is_durable_before_complete_manifest_is_published(tmp_path, monkeypatch):
    module, runtime, _, _ = fixture_runtime(tmp_path)
    synced = set()
    original_sync, original_write = module.os.fsync, module.atomic_write

    def sync(descriptor):
        item = module.os.fstat(descriptor)
        synced.add((item.st_dev, item.st_ino))
        original_sync(descriptor)

    def publish(path, value):
        if path.parent.name == "backup-manifests" and not path.name.endswith(".retention.json"):
            items = [runtime.paths.backup_dir, *runtime.paths.backup_dir.glob("*.orb")]
            assert len(items) == 4
            assert all((item.stat().st_dev, item.stat().st_ino) in synced for item in items)
        original_write(path, value)
    monkeypatch.setattr(module.os, "fsync", sync)
    monkeypatch.setattr(module, "atomic_write", publish)
    runtime.run()


# This executable simulates the Docker daemon boundary with actual independent
# worker processes. Killing its CLI does not kill those workers, exactly the
# lifecycle property the backup owner must handle. No Docker/network is used.
FAKE_DAEMON = r'''
import hashlib, json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path(os.environ['FAKE_DOCKER_STATE'])
root.mkdir(exist_ok=True)
def write(path, value):
    temporary = path.with_suffix('.tmp-' + str(os.getpid()))
    temporary.write_text(json.dumps(value)); os.replace(temporary, path)
def entries():
    return [json.loads(path.read_text()) for path in root.glob('*.container')]
def find(identity):
    return next((item for item in entries() if identity in (item['id'], item['name'].lstrip('/'))), None)
def save(item): write(root / (item['id'] + '.container'), item)
def live(pid):
    try:
        os.kill(pid, 0)
        state = subprocess.run(['/bin/ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith('Z')
    except ProcessLookupError: return False
def finish(item, code):
    item.update(running=False, status='exited', exit_code=code); save(item)
    (root / (item['id'] + '.done')).touch()
if sys.argv[1] == '--worker':
    item = find(sys.argv[2])
    def terminate(*_):
        time.sleep(0.2)
        finish(find(item['id']), 143)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    if item['role'] == os.environ.get('FAKE_HANG_ROLE'):
        while True:
            (root / (item['id'] + '.heartbeat')).write_text(str(time.time()))
            time.sleep(0.01)
    else:
        time.sleep(0.02)
        code = 7 if item['role'] == os.environ.get('FAKE_BAD_EXIT_ROLE') else 0
        finish(find(item['id']), code)
    raise SystemExit(code)
args = sys.argv[1:]
if args[:2] == ['volume', 'inspect']:
    print(json.dumps({'name':args[-1], 'driver':'local', 'options':{}})); raise SystemExit(0)
if args[:2] == ['volume', 'create']:
    print(args[-1]); raise SystemExit(0)
if args[0] == 'ps':
    filters = [args[index+1] for index, arg in enumerate(args[:-1]) if arg == '--filter']
    for item in entries():
        if all(not value.startswith('label=') or item['labels'].get(value[6:].split('=',1)[0]) == value[6:].split('=',1)[1] for value in filters):
            print(item['id'])
    raise SystemExit(0)
if args[0] == 'inspect':
    item = find(args[-1])
    if item is None: raise SystemExit(1)
    print(json.dumps(item)); raise SystemExit(0)
if args[0] in ('stop', 'kill'):
    item = find(args[-1])
    if item is None: raise SystemExit(1)
    if os.environ.get('FAKE_STOP_FAIL') == '1': raise SystemExit(2)
    if item.get('pid') and live(item['pid']): os.kill(item['pid'], signal.SIGTERM)
    deadline = time.monotonic() + 3
    while item.get('pid') and live(item['pid']) and time.monotonic() < deadline: time.sleep(0.01)
    raise SystemExit(0 if not item.get('pid') or not live(item['pid']) else 2)
if args[0] == 'rm':
    item = find(args[-1])
    if item is None: raise SystemExit(1)
    if item['running']:
        if '-f' not in args: raise SystemExit(2)
        os.kill(item['pid'], signal.SIGTERM)
        time.sleep(0.25)
    (root / (item['id'] + '.container')).unlink(); raise SystemExit(0)
if args[0] in ('create', 'run', 'exec'):
    labels = {args[index+1].split('=',1)[0]:args[index+1].split('=',1)[1]
              for index, arg in enumerate(args[:-1]) if arg == '--label'}
    role = labels.get('io.orbbec.backup.role')
    if not role:
        role = ('retention' if args[-1] == 'retention' else
                'replica-source' if args[0] == 'exec' and 'agent_platform' in args else
                'control-source' if args[0] == 'exec' else
                next((part.split('=')[-1].split('/')[-1].split('-')[0] + '-encryptor'
                      for part in args if part.startswith('PLATFORM_REPLICA_BACKUP_PATH=')), 'public-copy'))
    name = args[args.index('--name')+1] if '--name' in args else 'legacy-' + role
    identity = hashlib.sha256(name.encode()).hexdigest()
    item = {'id':identity, 'name':'/'+name, 'labels':labels, 'role':role,
            'image_id':next((value for value in args if value.startswith('sha256:')), ''),
            'running':False, 'status':'created', 'exit_code':0, 'arguments':args}
    save(item)
    if args[0] == 'create': print(identity); raise SystemExit(0)
elif args[0] == 'start':
    item = find(args[-1]); identity = item['id']; role = item['role']
else: raise SystemExit(3)
item.update(running=True, status='running'); save(item)
worker = subprocess.Popen([sys.executable, __file__, '--worker', identity], start_new_session=True,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
item['pid'] = worker.pid; save(item)
(root / (identity + '.pid')).write_text(str(worker.pid))
while not (root / (identity + '.done')).exists(): time.sleep(0.01)
item = find(identity)
if role.endswith('-encryptor'):
    sys.stdin.buffer.read()
    setting = next(value for value in item['arguments'] if value.startswith('PLATFORM_REPLICA_BACKUP_PATH='))
    path = pathlib.Path(os.environ['FAKE_BACKUP_DIR']) / pathlib.Path(setting.split('=',1)[1]).name
    path.write_bytes(b'synthetic-ciphertext'); path.chmod(0o600)
    print('{"status":"backed_up","encrypted_size":20,"plaintext_size":12}')
elif role == 'retention': print('{"status":"completed","dry_run":false,"session_count":0,"agent_count":0}')
elif role.endswith('-source'): sys.stdout.buffer.write(b'synthetic-input')
raise SystemExit(0 if os.environ.get('FAKE_CLI_SUCCESS') == '1' else item['exit_code'])
'''


LIFECYCLE_HARNESS = r'''
import importlib.util, json, os, pathlib, sys
spec = importlib.util.spec_from_file_location('cloud_backup_runtime', sys.argv[1])
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
root = pathlib.Path(sys.argv[2])
paths = module.BackupPaths(*(root / name for name in ('private','backups','attachments','postgres')))
class Runner(module.ProcessRunner):
    def capture(self, command, *, stage, timeout=60):
        if 'start' in command or 'run' in command: timeout = float(os.environ.get('FAKE_JOB_TIMEOUT', '30'))
        return super().capture(command, stage=stage, timeout=timeout)
    def pipeline(self, producer, encryptor, *, stage, timeout=1800):
        return super().pipeline(producer, encryptor, stage=stage,
                                timeout=float(os.environ.get('FAKE_JOB_TIMEOUT', '30')))
runtime = module.RuntimeBackup(paths, runner=Runner()); runtime.docker = sys.argv[3]
snapshot = {'containers': {
    'platform-api': {'image_id':'sha256:'+'4'*64},
    'platform-postgres': {'id':'2'*64,'image_id':'sha256:'+'5'*64,
                          'networks':{module.INTERNAL_NETWORK:{'IPAddress':'172.30.0.2'}}}},
    'postgres_credential': {'path':str(root/'synthetic-password')}}
runtime.resolve = lambda: snapshot
runtime._unchanged = lambda _: None
if os.environ.get('FAKE_NATIVE_PRODUCER'):
    def native_capture():
        fifo = root / 'file-list'; os.mkfifo(fifo)
        program = "import os,pathlib,subprocess,sys; child=subprocess.Popen(['/usr/bin/tar','-cf','-','-T',sys.argv[1]]); pathlib.Path(sys.argv[2]).write_text(str(os.getpid())+' '+str(child.pid)); child.wait()"
        return runtime._artifact(snapshot, 'native-test', 'attachments', 'minio_data_directory',
                                 [sys.executable, '-c', program, str(fifo), str(root/'native-pids')])
    runtime._run_locked = native_capture
try: runtime.run()
except module.BackupError as error:
    print('SAFE_STAGE='+error.stage, flush=True)
    raise SystemExit(1)
'''


def _pid_live(pid):
    try:
        os.kill(pid, 0)
        state = subprocess.run(['/bin/ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith('Z')
    except ProcessLookupError:
        return False


def _lifecycle_fixture(tmp_path, role, *, timeout=False, cleanup_fails=False):
    for name in ('private', 'backups', 'attachments', 'postgres', 'daemon'):
        (tmp_path / name).mkdir(mode=0o700)
    (tmp_path / 'private/backup-recovery-x25519.pub').write_bytes(b'p' * 32)
    (tmp_path / 'private/backup-recovery-x25519.pub').chmod(0o600)
    (tmp_path / 'synthetic-password').write_text('synthetic-password')
    docker = tmp_path / 'docker'
    docker.write_text(f'#!{sys.executable}\n' + FAKE_DAEMON); docker.chmod(0o700)
    harness = tmp_path / 'harness.py'; harness.write_text(LIFECYCLE_HARNESS)
    environment = {**os.environ, 'FAKE_DOCKER_STATE': str(tmp_path / 'daemon'),
                   'FAKE_BACKUP_DIR': str(tmp_path / 'backups'), 'FAKE_HANG_ROLE': role,
                   'FAKE_JOB_TIMEOUT': '0.8' if timeout else '30',
                   'FAKE_STOP_FAIL': '1' if cleanup_fails else '0'}
    command = [sys.executable, str(harness), str(HELPER), str(tmp_path), str(docker)]
    return command, environment


def _cleanup_fake_workers(tmp_path):
    for path in (tmp_path / 'daemon').glob('*.pid'):
        pid = int(path.read_text())
        if _pid_live(pid):
            os.killpg(pid, signal.SIGKILL)


@pytest.mark.parametrize('role', ['public-copy', 'replica-source', 'replica-encryptor', 'retention'])
@pytest.mark.parametrize('stop', ['timeout', 'sigterm'])
def test_daemon_workers_stop_before_flock_releases_on_timeout_or_sigterm(tmp_path, role, stop):
    command, environment = _lifecycle_fixture(tmp_path, role, timeout=stop == 'timeout')
    shared = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    worker_pid = None
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            beats = list((tmp_path / 'daemon').glob('*.heartbeat'))
            if beats:
                worker_pid = int(beats[0].with_suffix('.pid').read_text())
                break
            if process.poll() is not None:
                raise AssertionError(process.communicate())
            time.sleep(0.02)
        assert worker_pid is not None, 'target worker never started'
        if stop == 'sigterm': process.send_signal(signal.SIGTERM)
        lock = os.open(tmp_path / 'private/backup.lock', os.O_WRONLY)
        try:
            while process.poll() is None:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    assert not _pid_live(worker_pid), 'flock released while daemon worker is alive'
                    break
                except BlockingIOError:
                    pass
                time.sleep(0.01)
        finally:
            os.close(lock)
        output, error = process.communicate(timeout=12)
        assert process.returncode != 0, (output, error)
        assert not _pid_live(worker_pid), 'daemon worker survived failed helper'
        assert _pid_live(shared.pid), 'unrelated/shared process was terminated'
        assert not (tmp_path / 'private/backup-inflight.json').exists()
        if role == 'retention':
            assert (tmp_path / 'private/last-backup-success').exists()
            receipt = json.loads((tmp_path / 'private/last-backup-retention.json').read_text())
            assert receipt['backup_status'] == 'complete' and receipt['status'] == 'failed'
    finally:
        if process.poll() is None: process.kill()
        process.communicate()
        _cleanup_fake_workers(tmp_path)
        shared.terminate(); shared.wait()


def test_unconfirmed_daemon_cleanup_leaves_journal_and_denies_next_backup(tmp_path):
    command, environment = _lifecycle_fixture(tmp_path, 'retention', timeout=True, cleanup_fails=True)
    try:
        first = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
        assert first.returncode != 0
        journal = tmp_path / 'private/backup-inflight.json'
        assert journal.exists(), 'unconfirmed work has no durable admission block'
        marker = (tmp_path / 'private/last-backup-success').read_bytes()
        second = subprocess.run(command, env={**environment, 'FAKE_HANG_ROLE':''},
                                capture_output=True, text=True, timeout=5)
        assert second.returncode != 0 and 'unresolved_work' in second.stdout
        assert (tmp_path / 'private/last-backup-success').read_bytes() == marker
    finally:
        _cleanup_fake_workers(tmp_path)


@pytest.mark.parametrize('role', ['public-copy', 'replica-source', 'replica-encryptor', 'retention'])
def test_daemon_exit_code_rejects_false_success_from_start_attach_cli(tmp_path, role):
    command, environment = _lifecycle_fixture(tmp_path, '')
    marker = tmp_path / 'private/last-backup-success'
    marker.write_text('previous complete set\n'); marker.chmod(0o600)
    try:
        result = subprocess.run(command, env={**environment, 'FAKE_BAD_EXIT_ROLE':role, 'FAKE_CLI_SUCCESS':'1'},
                                capture_output=True, text=True, timeout=15)
        assert result.returncode != 0, result.stdout
        failure = json.loads((tmp_path / 'private/last-backup-failure.json').read_text())
        execution = next(item for item in failure['lifecycle']['containers'] if item['role'] == role)
        assert execution['exit_code'] == 7
        assert execution['removed'] is True
        if role == 'retention':
            assert marker.read_text() != 'previous complete set\n'
            receipt = json.loads((tmp_path / 'private/last-backup-retention.json').read_text())
            assert receipt['backup_status'] == 'complete' and receipt['execution']['exit_code'] == 7
        else:
            assert marker.read_text() == 'previous complete set\n'
        assert not (tmp_path / 'private/backup-inflight.json').exists()
    finally:
        _cleanup_fake_workers(tmp_path)


@pytest.mark.parametrize('stop', ['timeout', 'sigterm'])
def test_native_tar_process_group_is_reaped_before_helper_exits(tmp_path, stop):
    command, environment = _lifecycle_fixture(tmp_path, '', timeout=stop == 'timeout')
    process = subprocess.Popen(command, env={**environment, 'FAKE_NATIVE_PRODUCER':'1'},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    native_pids = []
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not (tmp_path / 'native-pids').exists():
            if process.poll() is not None: raise AssertionError(process.communicate())
            time.sleep(0.02)
        native_pids = list(map(int, (tmp_path / 'native-pids').read_text().split()))
        assert all(_pid_live(pid) for pid in native_pids)
        if stop == 'sigterm': process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=12)
        assert process.returncode != 0, (output, error)
        assert not any(_pid_live(pid) for pid in native_pids)
        assert not (tmp_path / 'private/backup-inflight.json').exists()
    finally:
        if process.poll() is None: process.kill()
        process.communicate()
        for pid in native_pids:
            if _pid_live(pid): os.kill(pid, signal.SIGKILL)
        _cleanup_fake_workers(tmp_path)


def test_postgres_client_uses_only_exact_readonly_credential_and_host_never_reads_it(tmp_path, monkeypatch):
    module, runtime, runner, _ = fixture_runtime(tmp_path)
    original_open = module.os.open

    def guarded_open(path, *args, **kwargs):
        assert Path(path) != module.POSTGRES_SECRET_PATH, 'host attempted password read'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(module.os, 'open', guarded_open)
    manifest = runtime.run()
    for role in ('replica-source', 'control-source'):
        command = container_command(runner, role)
        assert '999:999' in command and '--read-only' in command
        assert 'ALL' in command and 'no-new-privileges:true' in command
        assert command[command.index('--network') + 1] == 'container:' + '2' * 64
        assert 'PGHOST=172.30.0.2' in command
        assert 'PGREQUIREAUTH=scram-sha-256' in command
        assert 'PGOPTIONS=-c default_transaction_read_only=on -c application_name=platform-encrypted-backup' in command
        assert 'PGPASSFILE=/tmp/pgpass' in command
        assert 'type=bind,source=' + str(module.POSTGRES_SECRET_PATH) + ',target=/run/pg-password,readonly' in command
        assert not any('postgres-secrets:/' in part for part in command)
        assert '--rm' not in command
    assert all(item['exit_code'] == 0 and item['removed'] for item in manifest['lifecycle']['containers'])
    assert not any('com.docker.compose.service=' in part for command, _ in runner.commands
                   if command[1] == 'create' for part in command)


@pytest.mark.parametrize('password', ['synthetic:a\\b\n', 'two\nlines\n', 'contains\rreturn\n'])
def test_pgpass_is_escaped_inside_client_tmpfs_and_not_exported(tmp_path, password):
    module = runtime_module()
    secret = tmp_path / 'password'; secret.write_text(password); secret.chmod(0o400)
    pgpass = tmp_path / 'pgpass'
    dump = tmp_path / 'pg_dump'
    observed = tmp_path / 'observed.json'
    dump.write_text(f'#!{sys.executable}\n' + 'import json,os,pathlib,sys\n'
                    'pathlib.Path(os.environ["TEST_OBSERVED"]).write_text(json.dumps({"args":sys.argv,"password_env":os.environ.get("PGPASSWORD"),"pgpass":pathlib.Path(os.environ["PGPASSFILE"]).read_text()}))\n')
    dump.chmod(0o700)
    environment = {**os.environ, 'PATH':str(tmp_path) + os.pathsep + os.environ['PATH'],
                   'PG_SOURCE_PASSWORD_FILE':str(secret), 'PGPASSFILE':str(pgpass),
                   'PGHOST':'fd00::2', 'PGPORT':'5432', 'PGUSER':'platform_owner',
                   'TEST_OBSERVED':str(observed)}
    environment.pop('PGPASSWORD', None)
    result = subprocess.run(['/bin/sh', '-c', module.PG_DUMP_CLIENT, 'backup-pg-client', 'agent_platform'],
                            env=environment, capture_output=True, text=True)
    if password.startswith('synthetic:'):
        assert result.returncode == 0, result.stderr
        value = json.loads(observed.read_text())
        assert value['pgpass'] == 'fd00\\:\\:2:5432:*:platform_owner:synthetic\\:a\\\\b\n'
        assert value['password_env'] is None
        assert 'synthetic:a\\b' not in str(value['args'])
        assert pgpass.stat().st_mode & 0o777 == 0o600
    else:
        assert result.returncode != 0
        assert not observed.exists()


@pytest.mark.parametrize('retention_status', ['completed', 'pending', 'failed'])
def test_readonly_health_check_distinguishes_active_owner_from_unresolved_journal(tmp_path, retention_status):
    module, runtime, runner, _ = fixture_runtime(tmp_path)
    manifest = runtime.run()
    receipt_path = runtime.paths.private_dir / 'backup-manifests' / (manifest['set_id'] + '.retention.json')
    if retention_status == 'pending':
        receipt_path.unlink()
    elif retention_status == 'failed':
        receipt = json.loads(receipt_path.read_text()); receipt['status'] = 'failed'
        receipt_path.write_text(json.dumps(receipt))
    journal = runtime.paths.private_dir / 'backup-inflight.json'
    journal.write_text('{"attempt_id":"synthetic-active-job"}'); journal.chmod(0o600)
    runner.commands.clear()
    descriptor = os.open(runtime.paths.private_dir / 'backup.lock', os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = runtime.check(max_age_seconds=26 * 3600)
        assert result['status'] == ('in_progress' if retention_status == 'pending' else 'fresh')
        assert result['backup_status'] == 'fresh' and result['active_job'] is True
        assert result['retention_status'] == retention_status
        assert runner.commands == []
    finally:
        os.close(descriptor)
    with pytest.raises(module.BackupError, match='unresolved_work'):
        runtime.check(max_age_seconds=26 * 3600)
