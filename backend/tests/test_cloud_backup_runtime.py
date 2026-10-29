"""Runtime metadata and pipeline boundary tests; no Docker or production access."""
from copy import deepcopy
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

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


class FakeRunner:
    def __init__(self, paths, configs):
        self.paths, self.configs = paths, configs
        self.commands, self.pipelines = [], []
        self.after_pipeline = None
        self.retention_fails = False
        self.producer_fails = False
        self.encryptor_fails = False
        self.containers = {}
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
                "mounts": [], "networks": {"orbbec-agent-platform-internal": {}},
            }
        self.containers["platform-postgres"]["mounts"] = [{
            "Type": "bind", "Source": str(paths.postgres_dir),
            "Destination": "/var/lib/postgresql/data", "RW": True,
        }]
        self.containers["platform-minio"]["mounts"] = [{
            "Type": "bind", "Source": str(paths.attachment_dir),
            "Destination": "/data", "RW": True,
        }]

    def capture(self, command, *, stage, timeout=60):
        self.commands.append((tuple(command), stage))
        args = list(command[1:])
        if args[0] == "ps":
            service = next(item.split("=", 2)[-1] for item in args
                           if item.startswith("label=com.docker.compose.service="))
            return (self.containers[service]["id"] + "\n").encode()
        if args[0] == "inspect":
            return json.dumps(next(value for value in self.containers.values()
                                   if value["id"] == args[-1])).encode()
        if args[:2] == ["image", "inspect"]:
            return json.dumps({"id": args[-1], "tags": [
                "orbbec-agent-platform:manufacturing-v2-74c7e6b4-c4773a0d"
            ]}).encode()
        if args[:2] == ["volume", "inspect"]:
            name = args[-1]
            return json.dumps({"name": name, "driver": "local", "options": (
                {"type": "none", "o": "bind", "device": str(self.paths.backup_dir)}
                if name.endswith("-backups") else {}
            )}).encode()
        if args[:2] == ["volume", "create"]:
            return (args[-1] + "\n").encode()
        if args[0] == "run":
            if args[-1] == "retention":
                if self.retention_fails:
                    raise sys.modules["cloud_backup_runtime"].BackupError("retention")
                return b'{"status":"completed","dry_run":false,"session_count":1,"agent_count":0}'
            return b""
        if args[:2] == ["rm", "-f"]:
            return b""
        raise AssertionError((args, stage))

    def pipeline(self, producer, encryptor, *, stage, timeout=1800):
        self.pipelines.append((tuple(producer), tuple(encryptor), stage))
        module = sys.modules["cloud_backup_runtime"]
        if self.producer_fails:
            raise module.BackupError(stage + "_producer")
        if self.encryptor_fails:
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
    assert runner.pipelines[0][0][2] == "2" * 64
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
    command = next(command for command, stage in runner.commands if stage == "secret_copy" and "run" in command)
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
