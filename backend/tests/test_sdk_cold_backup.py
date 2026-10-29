"""SDK cold-capture lifecycle tests; source containers are entirely synthetic."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy/sdk/backup_sdk.py"


def sdk_module():
    assert SCRIPT.is_file(), "SDK cold backup coordinator is missing"
    spec = importlib.util.spec_from_file_location("sdk_cold_backup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeHost:
    def __init__(self):
        self.external_writer = False
        self.tree_gone = True
        self.http_ready = True

    def tree(self, value):
        return {"pids": [[value["pid"], "synthetic-start"]], "cgroup": None}

    def tree_exited(self, tree):
        return self.tree_gone

    def writers(self, roots, ignored_pids):
        return [{"pid": 999}] if self.external_writer else []

    def web_ready(self):
        return self.http_ready


class FakeRunner:
    def __init__(self, module, paths, sources):
        self.module, self.paths = module, paths
        self.sources = deepcopy(sources)
        self.calls, self.pipelines, self.helpers = [], [], {}
        self.fail_capture = False
        self.fail_cleanup = False
        self.db_exit = 0
        self.shutdown_marker = True
        self.stop_client_timeout = False
        self.db_never_ready = False
        self.after_pipeline = None
        self.groups_clear = True
        self.event_scheduler = "OFF"
        self.baseline_bad = False
        self.fail_inspect_after_stop = None
        self.inspect_failure_pending = None
        self.start_client_timeout = False
        self.helper_scope_bad = False
        self.external = {}
        self.volume_options = None
        self.pubhash = hashlib.sha256(paths.public_key.read_bytes()).hexdigest()

    def native_stopped(self):
        return self.groups_clear

    def capture(self, command, *, stage, timeout=60):
        self.calls.append(tuple(command))
        args = command[1:]
        if "--shutdown-proof" in command:
            return json.dumps({"shutdown_complete": self.shutdown_marker}).encode()
        if "--db-proof" in command:
            return json.dumps({
                "event_scheduler": self.event_scheduler,
                "objects": [
                    {"schema": "zentao", "table": "synthetic_base", "type": "BASE TABLE", "engine": "InnoDB", "rows": 7},
                    {"schema": "zentao", "table": "synthetic_view", "type": "VIEW", "engine": "", "rows": 3},
                ] + ([{"schema": "zentao", "table": "unexpected", "type": "VIEW", "rows": 0}]
                     if self.baseline_bad else []),
                "transaction": "repeatable_read_read_only",
            }).encode()
        if command[0] != "/usr/bin/docker":
            raise AssertionError("unexpected native command in fake")
        if args[0] == "inspect":
            identity = args[-1]
            if self.inspect_failure_pending == identity:
                self.inspect_failure_pending = None
                raise self.module.runtime.BackupError(stage)
            value = next((v for v in self.sources.values() if v["id"] == identity), None)
            if value is None:
                value = self.helpers.get(identity) or next(
                    (v for v in self.helpers.values() if v["name"] == "/" + identity), None)
            value = value or self.external.get(identity)
            if value is None:
                raise self.module.runtime.BackupError(stage)
            return json.dumps(value).encode()
        if args[:2] == ["image", "inspect"]:
            return json.dumps({"id": args[-1]}).encode()
        if args[:2] == ["volume", "inspect"]:
            name = args[-1]
            source = next((s["mounts"][0]["Source"] for s in self.sources.values()
                           if s["mounts"][0]["Name"] == name), str(self.paths.public_key.parent))
            return json.dumps({"name": name, "driver": "local", "options": self.volume_options, "mountpoint": source}).encode()
        if args[0] == "exec":
            path = args[-1]
            return (self.module.CONFIG_HASHES[path] + "  " + path + "\n").encode()
        if args[0] == "stop":
            value = self.helpers.get(args[-1]) or next(s for s in self.sources.values() if s["id"] == args[-1])
            value.update(running=False, status="exited", pid=0, exit_code=(
                self.db_exit if value["id"] == self.sources["db"]["id"] else 0),
                finished_at="2026-10-29T04:00:01Z")
            if self.fail_inspect_after_stop and value["id"] == self.sources[self.fail_inspect_after_stop]["id"]:
                self.inspect_failure_pending = value["id"]
            if self.stop_client_timeout and value["id"] in {s["id"] for s in self.sources.values()}:
                raise self.module.runtime.BackupError(stage)
            return (value["id"] + "\n").encode()
        if args[0] == "start":
            identity = args[-1]
            if identity in self.helpers:
                value = self.helpers[identity]
                value.update(running=False, status="exited", exit_code=0)
                return json.dumps({"sha256": self.pubhash, "size_bytes": 32}).encode()
            value = next(s for s in self.sources.values() if s["id"] == identity)
            value.update(running=True, status="running", pid=12345, exit_code=0,
                         health={"Status": "unhealthy" if self.db_never_ready else "healthy"})
            if self.start_client_timeout:
                raise self.module.runtime.BackupError(stage)
            return (identity + "\n").encode()
        if args[0] == "create":
            options = list(args)
            name = options[options.index("--name") + 1]
            labels = {}
            for i, item in enumerate(options):
                if item == "--label":
                    key, value = options[i + 1].split("=", 1)
                    labels[key] = value
            image = next(item for item in options if item.startswith("sha256:"))
            identity = format(len(self.helpers) + 100, "064x")
            bindings = [options[i + 1] for i, x in enumerate(options) if x == "-v"]
            mounts = []
            for binding in bindings:
                source, destination, *mode = binding.split(":")
                volume = source == self.module.PUBLIC_VOLUME
                mounts.append({"Type": "volume" if volume else "bind", "Name": source if volume else None,
                               "Source": str(self.paths.public_key.parent) if volume else source,
                               "Destination": destination, "RW": mode != ["ro"]})
            self.helpers[identity] = {
                "id": identity, "name": "/" + name, "image_id": image, "labels": labels,
                "running": False, "status": "created", "pid": 0, "exit_code": 0,
                "log_driver": "none", "user": "10001:10001", "mounts": mounts,
                "host_config": {"Privileged": False, "NetworkMode": "none", "ReadonlyRootfs": True,
                                "CapDrop": ["ALL"], "CapAdd": None,
                                "SecurityOpt": ["no-new-privileges:true"],
                                "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
                "_options": options,
            }
            if self.helper_scope_bad:
                self.helpers[identity]["host_config"]["Devices"] = [{"PathOnHost": "/dev/sda"}]
            return (identity + "\n").encode()
        if args[0] == "ps":
            if not any(x.startswith("label=io.orbbec.backup.attempt=") for x in args):
                return "".join(s["id"] + "\n" for s in [*self.sources.values(), *self.helpers.values(), *self.external.values()]).encode()
            attempt = next(x.split("=", 2)[-1] for x in args if x.startswith("label=io.orbbec.backup.attempt="))
            return "".join(v["id"] + "\n" for v in self.helpers.values()
                           if v["labels"]["io.orbbec.backup.attempt"] == attempt).encode()
        if args[0] == "rm":
            assert args[-1] not in {s["id"] for s in self.sources.values()}, "source must never be removed"
            if self.fail_cleanup:
                raise self.module.runtime.BackupError(stage)
            del self.helpers[args[-1]]
            return b""
        raise AssertionError(args)

    def pipeline(self, producer, encryptor, *, stage, timeout=1800):
        self.pipelines.append((tuple(producer), tuple(encryptor)))
        helper = self.helpers[encryptor[-1]]
        options = helper["_options"]
        filename = next(x.split("=", 1)[1].split("/")[-1] for x in options
                        if x.startswith("PLATFORM_REPLICA_BACKUP_PATH="))
        path = self.paths.ciphertext / filename
        path.write_bytes(b"synthetic ciphertext " * 50)
        path.chmod(0o600)
        helper.update(running=False, status="exited", exit_code=0)
        if self.after_pipeline:
            self.after_pipeline()
        if self.fail_capture:
            raise self.module.runtime.BackupError("capture_producer")
        return json.dumps({"status": "backed_up", "encrypted_size": 64, "plaintext_size": 128}).encode()


def fixture_sdk(tmp_path):
    module = sdk_module()
    work, env = tmp_path / "work", tmp_path / "deployment"
    for directory in (work, env, work / "private", work / "ciphertext"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("sdk.env", "sdk-compose.yaml"):
        (env / name).write_text("synthetic configuration")
        (env / name).chmod(0o600)
    pub = tmp_path / "public.key"
    pub.write_bytes(b"p" * 32)
    pub.chmod(0o600)
    (tmp_path / "recovery-public-key").write_bytes(b"p" * 32)
    (tmp_path / "recovery-public-key").chmod(0o600)
    paths = module.SDKPaths(work, env, pub, "sha256:" + "f" * 64, os.geteuid())
    sources = deepcopy(module.PINNED_SOURCES)
    for index, (role, value) in enumerate(sources.items()):
        data = tmp_path / ("data-" + role)
        data.mkdir(mode=0o700)
        (data / "synthetic").write_bytes(b"synthetic data")
        value["mounts"][0]["Source"] = str(data)
        value.update(running=True, status="running", pid=1000 + index, exit_code=0,
                     health={"Status": "healthy"}, oom_killed=False,
                     finished_at="", labels={
                         "com.docker.compose.project": module.PROJECT,
                         "com.docker.compose.service": "zentao" if role == "web" else "zentao-db",
                     }, user="", log_driver="json-file",
                     host_config={"Privileged": False, "NetworkMode": next(iter(value["networks"])),
                                  "PidMode": "", "IpcMode": "private", "VolumesFrom": None,
                                  "CapAdd": None, "Devices": [],
                                  "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                                  "PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18131"}]}
                                  if role == "web" else {}})
    runner = FakeRunner(module, paths, sources)
    host = FakeHost()
    tick = [0]
    def monotonic():
        tick[0] += 1000
        return tick[0]
    backup = module.SDKColdBackup(
        paths, runner=runner, host=host, sources=sources,
        clock=lambda: datetime(2026, 10, 29, 4, 0, tzinfo=UTC),
        monotonic=monotonic, sleep=lambda seconds: None,
        expected_base={("zentao", "synthetic_base")}, expected_views=1,
    )
    return module, backup, runner, host


def source_actions(runner, action):
    identities = {s["id"] for s in runner.sources.values()}
    return [call[-1] for call in runner.calls if len(call) > 1 and call[1] == action and call[-1] in identities]


def test_six_captures_use_only_cold_sources_then_restore_exact_original_ids(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    result = backup.run()
    assert result["status"] == "complete"
    assert [a["kind"] for a in result["artifacts"]] == list(module.ARTIFACT_KINDS)
    assert len(runner.pipelines) == 6
    assert source_actions(runner, "stop") == [runner.sources["web"]["id"], runner.sources["db"]["id"]]
    assert source_actions(runner, "start") == [runner.sources["db"]["id"], runner.sources["web"]["id"]]
    producers = [p for p, _ in runner.pipelines]
    assert sum("--copy-config" in p for p in producers) == 3
    for producer in producers[2:5]:
        assert producer[:4] == (sys.executable, str(SCRIPT.resolve()), "--copy-config", runner.sources["web"]["id"])
    assert all("exec" not in p for p in producers)
    assert not runner.helpers
    assert not backup.journal_path.exists()
    assert result["off_machine_sdk_dr_validated"] is False
    assert result["lifecycle"]["cleanup_confirmed"] is True
    assert result["quiesced_baseline"]["objects"][0]["rows"] == 7
    assert result["quiesced_baseline_evidence"]["sha256"]


@pytest.mark.parametrize("options", [None, {}])
def test_local_volume_identity_normalizes_only_known_empty_options(options):
    module = sdk_module()
    value = {"name": "fixed-volume", "driver": "local", "options": options, "mountpoint": "/fixed/data"}
    original = deepcopy(value)
    identity = module.local_volume_identity(value, "fixed-volume", mountpoint="/fixed/data")
    assert identity == {**value, "options": {}}
    assert value == original, "keep raw evidence separate from semantic identity"


@pytest.mark.parametrize("mutation", ["nonempty_options", "list_options", "false_options", "string_options",
                                     "missing_options", "extra_field", "name", "driver", "mountpoint", "relative"])
def test_local_volume_identity_rejects_unknown_or_changed_identity(mutation):
    module = sdk_module()
    value = {"name": "fixed-volume", "driver": "local", "options": None, "mountpoint": "/fixed/data"}
    if mutation == "nonempty_options":
        value["options"] = {"type": "none", "o": "bind", "device": "/other"}
    elif mutation == "list_options":
        value["options"] = []
    elif mutation == "false_options":
        value["options"] = False
    elif mutation == "string_options":
        value["options"] = ""
    elif mutation == "missing_options":
        del value["options"]
    elif mutation == "extra_field":
        value["unrecognized_identity"] = "unknown"
    else:
        value[mutation if mutation != "relative" else "mountpoint"] = {
            "name": "other-volume", "driver": "nfs", "mountpoint": "/other", "relative": "relative/data"}[mutation]
    with pytest.raises(module.SDKError, match="source_volume"):
        module.local_volume_identity(value, "fixed-volume", mountpoint="/fixed/data")


@pytest.mark.parametrize("options", [None, {}])
def test_entire_preflight_accepts_real_empty_volume_representations_without_lifecycle(tmp_path, options):
    _, backup, runner, _ = fixture_sdk(tmp_path)
    runner.volume_options = options
    runner.sources["web"]["health"] = None
    backup._preflight()
    assert backup.journal["phase"] == "preflight_verified"
    assert backup.lifecycle is None and not runner.helpers and not runner.pipelines
    assert not source_actions(runner, "stop") and not source_actions(runner, "start")
    checked_volumes = [call[-1] for call in runner.calls if call[1:3] == ("volume", "inspect")]
    assert len(checked_volumes) == 3


@pytest.mark.parametrize("scope", ["public", "source"])
def test_preflight_rejects_nonempty_options_for_every_volume_consumer(tmp_path, scope):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    original = runner.capture
    def capture(command, **kwargs):
        result = original(command, **kwargs)
        if command[1:3] == ["volume", "inspect"]:
            value = json.loads(result)
            if (value["name"] == module.PUBLIC_VOLUME) == (scope == "public"):
                value["options"] = {"o": "bind"}
                return json.dumps(value).encode()
        return result
    runner.capture = capture
    with pytest.raises(module.SDKError, match="public_key" if scope == "public" else "source_volume"):
        backup._preflight()
    assert not source_actions(runner, "stop") and not source_actions(runner, "start")


@pytest.mark.parametrize("mutation", ["image", "mount_field", "unhealthy", "mode"])
def test_preflight_rejects_drift_without_stopping_or_starting_sources(tmp_path, mutation):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    if mutation == "image":
        runner.sources["web"]["image_id"] = "sha256:" + "e" * 64
    elif mutation == "mount_field":
        runner.sources["db"]["mounts"][0]["Propagation"] = "shared"
    elif mutation == "unhealthy":
        runner.sources["db"]["health"] = {"Status": "unhealthy"}
    else:
        (backup.paths.deployment / "sdk.env").chmod(0o644)
    with pytest.raises(module.SDKError):
        backup.run()
    assert source_actions(runner, "start") == source_actions(runner, "stop") == []


@pytest.mark.parametrize("problem", ["exit137", "missing_shutdown", "capture", "interrupt"])
def test_backup_failure_still_restores_only_stopped_original_sources(tmp_path, problem):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    if problem == "exit137":
        runner.db_exit = 137
    elif problem == "missing_shutdown":
        runner.shutdown_marker = False
    elif problem == "capture":
        runner.fail_capture = True
    else:
        def interrupt():
            raise KeyboardInterrupt()
        runner.after_pipeline = interrupt
    with pytest.raises(module.SDKError):
        backup.run()
    assert source_actions(runner, "start") == [runner.sources["db"]["id"], runner.sources["web"]["id"]]
    assert len(runner.pipelines) == (1 if problem in {"capture", "interrupt"} else 0)
    assert all(s["running"] for s in runner.sources.values())
    assert not runner.helpers


def test_stop_cli_error_is_resolved_by_daemon_facts_not_assumed_unexecuted(tmp_path):
    _, backup, runner, _ = fixture_sdk(tmp_path)
    runner.stop_client_timeout = True
    report = backup.run()
    assert report["status"] == "complete"
    assert report["source_actions"]["db"]["stop_cli_ok"] is False
    assert report["source_actions"]["db"]["stopped_observed"] is True


def test_enabled_event_scheduler_fails_before_stopping_sources(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.event_scheduler = "ON"
    with pytest.raises(module.SDKError):
        backup.run()
    assert not source_actions(runner, "stop")
    assert not source_actions(runner, "start")


def test_quiesced_scope_failure_restores_web_without_stopping_running_db(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.baseline_bad = True
    with pytest.raises(module.SDKError):
        backup.run()
    assert source_actions(runner, "stop") == [runner.sources["web"]["id"]]
    assert source_actions(runner, "start") == [runner.sources["web"]["id"]]


def test_db_not_ready_never_starts_web_and_preserves_blocking_journal(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.db_never_ready = True
    with pytest.raises(module.SDKError):
        backup.run()
    assert source_actions(runner, "start") == [runner.sources["db"]["id"]]
    assert backup.journal_path.is_file()


def test_cleanup_error_does_not_prevent_safe_source_recovery(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.fail_cleanup = True
    with pytest.raises(module.SDKError):
        backup.run()
    assert source_actions(runner, "start") == [runner.sources["db"]["id"], runner.sources["web"]["id"]]
    assert backup.journal_path.is_file()
    assert all(s["running"] for s in runner.sources.values())


def test_readonly_native_cleanup_unknown_still_restores_services_but_blocks_success(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.groups_clear = False
    with pytest.raises(module.SDKError, match="native_cleanup_unconfirmed"):
        backup.run()
    assert source_actions(runner, "start") == [runner.sources["db"]["id"], runner.sources["web"]["id"]]
    journal = json.loads(backup.journal_path.read_text())
    assert journal["services_restored"] is True
    assert journal["native_producers_stopped"] is False
    assert journal["status"] == "failed" and journal["blocked"] is True


def test_inspect_collects_health_status_without_healthcheck_logs():
    module = sdk_module()
    assert "{{json .State.Health}}" not in module.INSPECT
    assert '{{with index .State "Health"}}' in module.INSPECT
    assert '{"Status":{{json .Status}}}' in module.INSPECT
    assert "Health.Log" not in module.INSPECT


@pytest.mark.skipif(not os.environ.get("SDK_COLD_DOCKER_FORMATTER_IMAGE"),
                    reason="explicit existing local image required for real Docker formatter contract")
def test_real_local_docker_formatter_accepts_absent_health_without_starting_container():
    module = sdk_module()
    docker = shutil.which("docker")
    assert docker, "Docker CLI required for explicit formatter test"
    image = os.environ["SDK_COLD_DOCKER_FORMATTER_IMAGE"]
    assert module.runtime.IMAGE_ID.fullmatch(image)
    environment = dict(os.environ)
    environment.pop("DOCKER_CONTEXT", None)
    endpoint = subprocess.check_output([docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                                       env=environment, text=True).strip()
    endpoint = json.loads(endpoint)
    assert endpoint.startswith("unix:///"), "formatter test refuses remote Docker endpoints"
    command = [docker, "--host", endpoint]
    owner, identity = uuid.uuid4().hex, None
    def run(*args):
        return subprocess.run([*command, *args], capture_output=True, text=True, env=environment, timeout=30)
    try:
        created = run("create", "--pull=never", "--name", "orbbec-sdk-formatter-test-" + owner,
                      "--label", "io.orbbec.sdk-formatter-test=" + owner,
                      "--network", "none", "--log-driver", "none", "--read-only", "--user", "999",
                      "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--no-healthcheck",
                      "--tmpfs", "/var/lib/mysql", "--tmpfs", "/run/mysqld", "--tmpfs", "/tmp",
                      "--entrypoint", "/bin/true", image)
        assert created.returncode == 0, created.stderr
        identity = created.stdout.strip()
        assert module.runtime.CONTAINER_ID.fullmatch(identity)
        labels = run("inspect", "--format", "{{json .Config.Labels}}", identity)
        assert labels.returncode == 0
        assert json.loads(labels.stdout)["io.orbbec.sdk-formatter-test"] == owner
        actual = run("inspect", "--format", module.INSPECT, identity)
        assert actual.returncode == 0, actual.stderr
        value = json.loads(actual.stdout)
        assert value["id"] == identity and value["image_id"] == image
        assert value["running"] is False and value["status"] == "created"
        assert value["health"] is None
        assert value["log_driver"] == "none" and value["host_config"]["NetworkMode"] == "none"
        assert all(mount["Type"] == "tmpfs" for mount in value["mounts"])
    finally:
        if identity and module.runtime.CONTAINER_ID.fullmatch(identity):
            owned = run("inspect", "--format", "{{json .Config.Labels}}", identity)
            assert owned.returncode == 0 and json.loads(owned.stdout).get("io.orbbec.sdk-formatter-test") == owner
            removed = run("rm", identity)
            assert removed.returncode == 0, removed.stderr


def test_existing_inflight_journal_refuses_new_attempt(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    backup.journal_path.write_text("{}")
    backup.journal_path.chmod(0o600)
    with pytest.raises(module.SDKError, match="unresolved"):
        backup.run()
    assert not source_actions(runner, "stop")


def test_source_journal_is_rechecked_after_acquiring_lock(tmp_path, monkeypatch):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    original = module.fcntl.flock
    def lock_then_leave_journal(*args):
        original(*args)
        backup.journal_path.write_text('{"previous_attempt":"unresolved"}')
        backup.journal_path.chmod(0o600)
    monkeypatch.setattr(module.fcntl, "flock", lock_then_leave_journal)
    with pytest.raises(module.SDKError, match="unresolved"):
        backup.run()
    assert not source_actions(runner, "stop")
    assert "previous_attempt" in backup.journal_path.read_text()


@pytest.mark.parametrize("role", ["web", "db"])
def test_recovery_reconciles_stop_intent_after_one_lost_daemon_observation(tmp_path, role):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.fail_inspect_after_stop = role
    with pytest.raises(module.SDKError):
        backup.run()
    assert source_actions(runner, "start") == (
        [runner.sources["web"]["id"]] if role == "web"
        else [runner.sources["db"]["id"], runner.sources["web"]["id"]]
    )
    assert all(value["running"] for value in runner.sources.values())


def test_restart_cli_timeout_is_not_equated_to_failed_service_recovery(tmp_path):
    _, backup, runner, _ = fixture_sdk(tmp_path)
    runner.start_client_timeout = True
    report = backup.run()
    assert report["status"] == "complete"
    assert all(report["source_actions"][role]["ready"] for role in ("db", "web"))
    assert not report["source_actions"]["db"]["restart_cli_ok"]


def test_helper_excessive_hostconfig_is_rejected_before_source_stop(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.helper_scope_bad = True
    with pytest.raises(module.SDKError, match="helper_scope"):
        backup.run()
    assert not source_actions(runner, "stop")


def test_quiesced_database_producer_uses_inner_timeout_and_readonly_rr(monkeypatch):
    module = sdk_module()
    calls = []
    schema = ("zentao\tsynthetic_base\tBASE TABLE\tInnoDB\n"
              "zentao\tsynthetic_view\tVIEW\t\n")
    def run(command, **kwargs):
        calls.append((command, kwargs["input"]))
        assert 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD"' in command[-1]
        assert "exec timeout --signal=TERM --kill-after=5s 120s mariadb" in command[-1]
        query = kwargs["input"]
        if "information_schema.TABLES" in query:
            output = schema
        elif "START TRANSACTION" in query:
            assert "SET TRANSACTION READ ONLY" in query
            assert "WITH CONSISTENT SNAPSHOT" in query
            output = "0\t7\n1\t3\n"
        else:
            output = "OFF\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    monkeypatch.setattr(module.subprocess, "run", run)
    result = module.database_proof(module.PINNED_SOURCES["db"]["id"], "quiesced")
    assert [item["rows"] for item in result["objects"]] == [7, 3]
    assert result["objects"][1]["type"] == "VIEW"
    assert all(command[:3] == ["/usr/bin/docker", "exec", "-i"] for command, _ in calls)


@pytest.mark.parametrize("role", ["web", "db"])
def test_journal_failure_before_stop_call_does_not_leave_services_down(tmp_path, monkeypatch, role):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    original = module.runtime.atomic_write
    failed = []
    def write(path, content):
        value = json.loads(content)
        if value.get("phase") == role + "_stop_intent" and not failed:
            failed.append(True)
            raise OSError("synthetic disk failure")
        return original(path, content)
    monkeypatch.setattr(module.runtime, "atomic_write", write)
    with pytest.raises(module.SDKError):
        backup.run()
    assert all(source["running"] for source in runner.sources.values())
    assert source_actions(runner, "stop") == ([] if role == "web" else [runner.sources["web"]["id"]])
    assert not backup.journal_path.exists()


@pytest.mark.parametrize("line,expected", [
    ("2026-10-29T04:00:01Z 2026-10-29 04:00:01 0 [Note] mariadbd: Shutdown complete\n", True),
    ("2026-10-28T04:00:01Z 2026-10-28 04:00:01 0 [Note] mariadbd: Shutdown complete\n", False),
    ("2026-10-29T04:00:01Z 2026-10-29 04:00:01 0 [ERROR] query contains mariadbd: Shutdown complete\n", False),
])
def test_shutdown_proof_requires_this_window_normal_mariadb_note(monkeypatch, line, expected):
    module = sdk_module()
    monkeypatch.setattr(module.subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=line))
    result = module.shutdown_proof(module.PINNED_SOURCES["db"]["id"],
                                   "2026-10-29T04:00:00Z", "2026-10-29T04:00:02Z")
    assert result["shutdown_complete"] is expected


MARIADB_106_SHUTDOWN = "2026-10-29T04:36:40.040643105Z 2026-10-29  4:36:40 0 [Note] mariadbd: Shutdown complete\n"


@pytest.mark.parametrize("line,expected", [
    (MARIADB_106_SHUTDOWN, True),
    (MARIADB_106_SHUTDOWN.replace("  4:", " 04:"), True),
    (MARIADB_106_SHUTDOWN.replace("  4:", "\t4:"), True),
    (MARIADB_106_SHUTDOWN.replace("mariadbd:", "mysqld:"), True),
    (MARIADB_106_SHUTDOWN.replace("mariadbd:", "another-daemon:"), False),
    (MARIADB_106_SHUTDOWN.replace("[Note]", "[ERROR]"), False),
    (MARIADB_106_SHUTDOWN.replace("[Note]", "query text contains [Note]"), False),
    (MARIADB_106_SHUTDOWN.replace("mariadbd: Shutdown complete", "InnoDB: Shutdown completed"), False),
    (MARIADB_106_SHUTDOWN.replace("Shutdown complete\n", "Shutdown complete extra\n"), False),
    (MARIADB_106_SHUTDOWN.replace("T04:36:40.040643105Z", "T04:35:40.040643105Z"), False),
    (MARIADB_106_SHUTDOWN.replace("T04:36:40.040643105Z", "T04:36:50.040643105Z"), False),
    (MARIADB_106_SHUTDOWN.replace(".040643105Z ", ".040643105 "), False),
])
def test_shutdown_proof_matches_vendor_106_format_but_keeps_exact_marker_and_outer_window(monkeypatch, line, expected):
    module = sdk_module()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=line)
    monkeypatch.setattr(module.subprocess, "run", run)
    result = module.shutdown_proof(module.PINNED_SOURCES["db"]["id"],
                                   "2026-10-29T04:36:32.540812Z", "2026-10-29T04:36:45Z")
    assert result["shutdown_complete"] is expected
    assert calls[0][1:3] == ["logs", "--timestamps"]
    assert calls[0][-1] == module.PINNED_SOURCES["db"]["id"]


def test_external_writer_or_live_process_tree_refuses_cold_capture(tmp_path):
    module, backup, runner, host = fixture_sdk(tmp_path)
    host.tree_gone = False
    with pytest.raises(module.SDKError):
        backup.run()
    assert not runner.pipelines
    assert source_actions(runner, "start") == [runner.sources["web"]["id"]]


def test_mount_set_permutation_is_allowed_but_all_fields_are_retained(tmp_path):
    _, backup, runner, _ = fixture_sdk(tmp_path)
    source = runner.sources["web"]
    source["mounts"] = list(reversed(source["mounts"]))
    report = backup.run()
    assert report["sources"]["web"]["mounts"][0]["Propagation"] == ""


def test_source_identity_change_after_capture_never_starts_unknown_db(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    def change():
        runner.sources["db"]["image_id"] = "sha256:" + "e" * 64
    runner.after_pipeline = change
    with pytest.raises(module.SDKError):
        backup.run()
    assert not source_actions(runner, "start")
    assert backup.journal_path.is_file()


def test_any_late_cold_volume_write_invalidates_entire_set(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    def mutate_after_other_volume():
        if len(runner.pipelines) == 2:
            path = Path(runner.sources["db"]["mounts"][0]["Source"]) / "synthetic"
            path.write_bytes(b"external cold write")
    runner.after_pipeline = mutate_after_other_volume
    with pytest.raises(module.SDKError, match="cold_directory_changed"):
        backup.run()
    assert source_actions(runner, "start") == [runner.sources["db"]["id"], runner.sources["web"]["id"]]


def test_detected_external_writer_refuses_preflight_without_source_actions(tmp_path):
    module, backup, runner, host = fixture_sdk(tmp_path)
    host.external_writer = True
    with pytest.raises(module.SDKError, match="external_writer"):
        backup.run()
    assert not source_actions(runner, "stop")
    assert not source_actions(runner, "start")


def test_public_volume_key_change_invalidates_cold_set_and_restores_sources(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.after_pipeline = lambda: (backup.paths.public_key.parent / "recovery-public-key").write_bytes(b"x" * 32)
    with pytest.raises(module.SDKError, match="public_key"):
        backup.run()
    assert source_actions(runner, "start") == [runner.sources["db"]["id"], runner.sources["web"]["id"]]


def test_new_db_network_peer_is_a_potential_writer_even_without_volume_mount(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    runner.external["d" * 64] = {
        "id": "d" * 64, "mounts": [], "networks": runner.sources["db"]["networks"]}
    with pytest.raises(module.SDKError, match="external_db_peer"):
        backup.run()
    assert not source_actions(runner, "stop")


def test_quiesced_receipt_must_confirm_readonly_repeatable_read(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    original = backup._probe
    def bad_transaction(flag, *arguments):
        value = original(flag, *arguments)
        if flag == "--db-proof" and arguments == ("quiesced",):
            value["transaction"] = "autocommit"
        return value
    backup._probe = bad_transaction
    with pytest.raises(module.SDKError, match="quiesced_scope"):
        backup.run()
    assert source_actions(runner, "stop") == [runner.sources["web"]["id"]]
    assert source_actions(runner, "start") == [runner.sources["web"]["id"]]


def test_real_shared_pipeline_encrypts_without_plaintext_dump_files(tmp_path):
    module = sdk_module()
    from app.cloud_replica.backup import decrypt_stream
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    import io
    key = X25519PrivateKey.generate()
    public = tmp_path / "public"
    public.write_bytes(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    public.chmod(0o600)
    encrypted = tmp_path / "fixture.orb"
    producer = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'synthetic cold tar stream'*1000)"]
    encryptor = ["/usr/bin/env", "PYTHONPATH=" + str(ROOT / "backend"),
                 "PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE=" + str(public),
                 "PLATFORM_REPLICA_BACKUP_PATH=" + str(encrypted),
                 sys.executable, "-m", "app.cloud_replica.cli", "backup"]
    runner = module.SDKProcessRunner()
    receipt = json.loads(runner.pipeline(producer, encryptor, stage="capture", timeout=30))
    assert receipt["status"] == "backed_up"
    assert runner.native_stopped()
    restored = io.BytesIO()
    with encrypted.open("rb") as source:
        decrypt_stream(source, restored, key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    assert restored.getvalue() == b"synthetic cold tar stream" * 1000
    assert {p.name for p in tmp_path.iterdir()} == {"public", "fixture.orb"}


def config_tar(*, name="config.php", content=b"synthetic private configuration", kind=tarfile.REGTYPE,
               extra=False, pax=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT if pax else tarfile.USTAR_FORMAT) as archive:
        item = tarfile.TarInfo(name)
        item.type = kind
        if pax:
            item.pax_headers = {"mtime": "1.25"}
        if kind == tarfile.REGTYPE:
            item.size = len(content)
        else:
            item.linkname = "config.php"
        archive.addfile(item, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
        if extra:
            archive.addfile(tarfile.TarInfo("extra"), io.BytesIO())
    return output.getvalue()


@pytest.mark.parametrize("pax", [False, True])
def test_config_stream_validates_actual_file_and_forwards_identical_archive(pax):
    module = sdk_module()
    value, destination = config_tar(pax=pax), io.BytesIO()
    module.validate_config_tar(io.BytesIO(value), destination, "config.php",
                               hashlib.sha256(b"synthetic private configuration").hexdigest())
    assert destination.getvalue() == value


@pytest.mark.parametrize("problem", ["changed", "path", "symlink", "hardlink", "extra", "oversize", "truncated", "trailing"])
def test_config_stream_rejects_unpinned_or_ambiguous_captured_bytes(problem):
    module = sdk_module()
    options = {"content": b"changed during shutdown"} if problem == "changed" else {}
    if problem == "path":
        options["name"] = "../config.php"
    if problem in {"symlink", "hardlink"}:
        options["kind"] = tarfile.SYMTYPE if problem == "symlink" else tarfile.LNKTYPE
    if problem == "extra":
        options["extra"] = True
    if problem == "oversize":
        module.MAX_CONFIG_BYTES = 4
    value = config_tar(**options)
    if problem == "truncated":
        value = value[:520]
    if problem == "trailing":
        value += b"unexpected nonzero tail"
    with pytest.raises(module.SDKError, match="configuration_archive"):
        module.validate_config_tar(io.BytesIO(value), io.BytesIO(), "config.php",
                                   hashlib.sha256(b"synthetic private configuration").hexdigest())


def test_copy_config_restricts_identity_path_and_checks_child_exit(monkeypatch):
    module = sdk_module()
    identity = module.PINNED_SOURCES["web"]["id"]
    path = next(iter(module.CONFIG_HASHES))
    content = b"synthetic private configuration"
    module.CONFIG_HASHES[path] = hashlib.sha256(content).hexdigest()
    calls = []
    class Child:
        stdout = io.BytesIO(config_tar(content=content))
        returncode = 7
        def wait(self, timeout=None):
            return self.returncode
        def poll(self):
            return self.returncode
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Child()
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    for wrong_id, wrong_path in (("0" * 64, path), (identity, "/unknown")):
        with pytest.raises(module.SDKError, match="configuration_scope"):
            module.copy_config(wrong_id, wrong_path, output=io.BytesIO())
    assert not calls
    with pytest.raises(module.SDKError, match="configuration_copy"):
        module.copy_config(identity, path, output=io.BytesIO())
    assert calls[0][0] == ["/usr/bin/docker", "cp", identity + ":" + path, "-"]
    assert calls[0][1]["stderr"] == subprocess.DEVNULL
    assert not calls[0][1].get("start_new_session"), "cp must stay in the producer's owned process group"


@pytest.mark.parametrize("role", ["web", "db"])
@pytest.mark.parametrize("interruption", ["inspect", "TERM", "TERM-during-CLI"])
def test_finally_waits_for_accepted_delayed_stop_before_same_id_recovery(tmp_path, role, interruption):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    instant, pending = [0], {}
    identity = runner.sources[role]["id"]
    original = runner.capture
    backup.monotonic = lambda: instant[0]
    backup.sleep = lambda seconds: instant.__setitem__(0, instant[0] + seconds)
    def capture(command, **kwargs):
        if command[1:2] == ["stop"] and command[-1] == identity:
            runner.calls.append(tuple(command))
            pending.update(at=instant[0] + 3, interrupted=False)
            if interruption == "TERM-during-CLI":
                pending["interrupted"] = True
                module.signal.raise_signal(module.signal.SIGTERM)
            return (identity + "\n").encode()
        if command[1:2] == ["inspect"] and command[-1] == identity and pending:
            if not pending["interrupted"]:
                pending["interrupted"] = True
                if interruption == "TERM":
                    module.signal.raise_signal(module.signal.SIGTERM)
                raise module.runtime.BackupError("capture")
            if instant[0] >= pending["at"]:
                runner.sources[role].update(running=False, status="exited", pid=0, exit_code=0)
                pending.clear()
        return original(command, **kwargs)
    runner.capture = capture
    with pytest.raises(module.SDKError):
        backup.run()
    assert instant[0] >= 3
    assert source_actions(runner, "stop").count(identity) == 1
    assert source_actions(runner, "start") == (
        [runner.sources["web"]["id"]] if role == "web"
        else [runner.sources["db"]["id"], runner.sources["web"]["id"]])
    assert all(value["running"] for value in runner.sources.values())
    assert backup.journal["source_actions"][role]["reconciled_in_finally"] is True
    assert not backup.journal_path.exists()


def test_pending_db_stop_observation_is_bounded_and_does_not_restart_web(tmp_path):
    module, backup, runner, _ = fixture_sdk(tmp_path)
    instant, pending = [0], [False]
    identity, original = runner.sources["db"]["id"], runner.capture
    backup.monotonic = lambda: instant[0]
    backup.sleep = lambda seconds: instant.__setitem__(0, instant[0] + seconds)
    def capture(command, **kwargs):
        if command[1:2] == ["stop"] and command[-1] == identity:
            runner.calls.append(tuple(command))
            pending[0] = True
            return b""
        if command[1:2] == ["inspect"] and command[-1] == identity and pending[0]:
            if pending[0] is True:
                pending[0] = "observing"
                raise module.runtime.BackupError("capture")
        return original(command, **kwargs)
    runner.capture = capture
    with pytest.raises(module.SDKError, match="db_stop_unresolved"):
        backup.run()
    assert instant[0] == module.DB_TIMEOUT + 35
    assert source_actions(runner, "stop").count(identity) == 1
    assert not source_actions(runner, "start")
    assert backup.journal_path.is_file()
    assert backup.journal["blocked"] is True


@pytest.mark.parametrize("changed", [False, True])
def test_real_config_producer_crypto_pipeline_rejects_shutdown_content_drift(tmp_path, changed):
    module = sdk_module()
    from app.cloud_replica.backup import decrypt_stream
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    key = X25519PrivateKey.generate()
    public, encrypted = tmp_path / "public", tmp_path / "fixture.orb"
    public.write_bytes(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    public.chmod(0o600)
    original = b"synthetic private configuration"
    value = config_tar(content=b"changed during shutdown" if changed else original)
    identity, path = module.PINNED_SOURCES["web"]["id"], next(iter(module.CONFIG_HASHES))
    # Only the Docker daemon boundary is synthetic. Run real child processes,
    # copy_config, tar validation, shared pipeline and original crypto CLI.
    child_source = "import sys;sys.stdout.buffer.write(bytes.fromhex(" + repr(value.hex()) + "))"
    producer_source = f"""
import importlib.util,subprocess,sys
spec=importlib.util.spec_from_file_location('sdk_test_producer',{str(SCRIPT)!r})
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
m.CONFIG_HASHES[{path!r}]={hashlib.sha256(original).hexdigest()!r}
popen=subprocess.Popen
def docker(command,**kwargs):
    assert command==[m.DOCKER,'cp',{identity + ':' + path!r},'-']
    return popen([sys.executable,'-c',{child_source!r}],**kwargs)
m.subprocess.Popen=docker
raise SystemExit(m.main(['--copy-config',{identity!r},{path!r}]))
"""
    encryptor = ["/usr/bin/env", "PYTHONPATH=" + str(ROOT / "backend"),
                 "PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE=" + str(public),
                 "PLATFORM_REPLICA_BACKUP_PATH=" + str(encrypted),
                 sys.executable, "-m", "app.cloud_replica.cli", "backup"]
    runner = module.SDKProcessRunner()
    if changed:
        with pytest.raises(module.runtime.BackupError, match="capture_producer"):
            runner.pipeline([sys.executable, "-c", producer_source], encryptor, stage="capture", timeout=30)
    else:
        receipt = json.loads(runner.pipeline([sys.executable, "-c", producer_source], encryptor, stage="capture", timeout=30))
        assert receipt["status"] == "backed_up"
        restored = io.BytesIO()
        with encrypted.open("rb") as source:
            decrypt_stream(source, restored, key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
        assert restored.getvalue() == value
    assert runner.native_stopped()
    assert {p.name for p in tmp_path.iterdir()} <= {"public", "fixture.orb"}
