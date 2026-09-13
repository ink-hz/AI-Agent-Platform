"""Host supervisor engineering only: owned processes/fake Docker, no production."""

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[2]
    / "artifacts/2026-09-13-hr-launch/production/attachment_hotfix.py"
)


def helper():
    assert SCRIPT.is_file(), "fixed attachment hotfix supervisor missing"
    spec = importlib.util.spec_from_file_location("attachment_hotfix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_drift_is_rejected_without_learning_a_new_baseline():
    h = helper()
    expected = {
        "api": {
            "id": "a",
            "image": "old",
            "running": True,
            "started_at": "before",
            "restart_count": 0,
        }
    }
    h.match_snapshot(expected, expected)
    for key, value in (
        ("id", "b"),
        ("image", "new"),
        ("started_at", "after"),
        ("restart_count", 1),
        ("running", False),
    ):
        changed = {"api": {**expected["api"], key: value}}
        with pytest.raises(h.HotfixFailure, match="peer_changed"):
            h.match_snapshot(expected, changed)


@pytest.mark.parametrize(
    "failure", ["migration_cleanup_unverified", "peer_changed", "command_timeout"]
)
def test_failure_after_old_stop_never_starts_new_or_restores_old(tmp_path, failure):
    h = helper()
    events = []

    class Local(h.Hotfix):
        def preflight(self):
            events.append("preflight")

        def acquire(self):
            events.append("locks")

        def stop_old(self):
            self.stopping_old = True
            events.append("old_restart_no_and_stop")

        def migrate(self):
            raise h.HotfixFailure(failure)

        def recreate(self):
            events.append("recreate")

        def fail_closed(self):
            events.append("keep_stopped_and_locks")
            return True

        def record(self, stage, **values):
            self.receipt.update(stage=stage, **values)

    runner = Local(tmp_path / "inputs", tmp_path / "helpers")
    assert runner.run() == 1
    assert events == [
        "preflight",
        "locks",
        "old_restart_no_and_stop",
        "keep_stopped_and_locks",
    ]
    assert runner.receipt["status"] == "failed"


@pytest.mark.parametrize(
    "number", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT]
)
def test_four_signals_reap_the_exact_local_command_process(tmp_path, number):
    helper()
    child_pid = tmp_path / "child.pid"
    entered = tmp_path / "entered"
    program = tmp_path / "owned.py"
    program.write_text("""import importlib.util,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("hotfix",sys.argv[1]); h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
for number in h.SIGNALS: signal.signal(number,h.interrupted)
Path(sys.argv[3]).write_text("entered")
try:
 h.command([sys.executable,"-c","import os,time; from pathlib import Path; Path("+repr(sys.argv[2])+").write_text(str(os.getpid())); time.sleep(60)"],timeout=60)
except h.HotfixInterrupted as e:
 raise SystemExit(128+e.number)
""")
    proc = subprocess.Popen(
        [sys.executable, str(program), str(SCRIPT), str(child_pid), str(entered)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 3
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        proc.send_signal(number)
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode == 128 + number, (stdout, stderr)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_command_timeout_reaps_owned_client_and_stays_failure(tmp_path):
    h = helper()
    pidfile = tmp_path / "pid"
    with pytest.raises(h.HotfixFailure, match="command_timeout"):
        h.command(
            [
                sys.executable,
                "-c",
                "import os,time;from pathlib import Path;Path("
                + repr(str(pidfile))
                + ").write_text(str(os.getpid()));time.sleep(60)",
            ],
            timeout=0.2,
        )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


@pytest.mark.parametrize(
    "row",
    [
        None,
        {
            "Id": "replacement",
            "Image": "wrong",
            "Name": "/other",
            "State": {"Running": False},
            "HostConfig": {"RestartPolicy": {"Name": "no"}},
        },
    ],
)
def test_old_stop_proof_rejects_absent_or_replaced_identity(tmp_path, row):
    h = helper()
    runner = h.Hotfix(tmp_path / "inputs", tmp_path / "helpers")
    runner.at = lambda _identity: row
    assert hasattr(runner, "verify_old_stopped"), (
        "strict pre-migration stopped identity missing"
    )
    with pytest.raises(h.HotfixFailure, match="old_stop_unverified"):
        runner.verify_old_stopped()


def test_daemon_failure_does_not_skip_revocation_of_proven_own_grant(tmp_path):
    h = helper()
    runner = h.Hotfix(tmp_path / "inputs", tmp_path / "helpers")
    runner.migration_started = True
    runner.child_receipt = lambda: {
        "container_name": "hr-migration-" + "a" * 36 + "-production",
        "events": [{"stage": "grant_pending", "environment": "production"}],
    }

    def unavailable(_identity):
        raise h.HotfixFailure("daemon_unavailable")

    runner.at = unavailable
    operations = []
    runner.admin = lambda sql, **kwargs: operations.append((sql, kwargs))
    runner.at_rest = lambda: operations.append("at_rest")
    assert runner.fail_closed() is False
    assert operations == [
        (
            "revoke platform_control_owner from platform_control_migrator",
            {"write": True},
        ),
        "at_rest",
    ]


def test_migration_entry_rechecks_old_id_before_spawning_helper(tmp_path, monkeypatch):
    h = helper()
    runner = h.Hotfix(tmp_path / "inputs", tmp_path / "helpers")
    runner.validate_locks = runner.check_inputs = lambda: None
    runner.at = lambda _identity: None
    spawned = []
    monkeypatch.setattr(h, "command", lambda *args, **kwargs: spawned.append(args))
    with pytest.raises(h.HotfixFailure, match="old_stop_unverified"):
        runner.migrate()
    assert not spawned and runner.migration_started is False


def test_new_id_receipt_write_failure_never_reaches_docker_start(tmp_path):
    h = helper()
    runner = h.Hotfix(tmp_path / "inputs", tmp_path / "helpers")
    runner.validate_locks = runner.check_inputs = runner.verify_old_stopped = lambda: (
        None
    )
    called = []
    new_id = "b" * 64

    def compose(*args):
        called.append(args)
        return new_id if args[0] == "ps" else ""

    runner.compose = compose
    runner.at = lambda _id: {
        "Id": new_id,
        "Image": h.IMAGE,
        "Name": "/" + h.SERVICE,
        "State": {"Running": False},
        "Config": {
            "Labels": {
                "com.docker.compose.project": "orbbec-agent-platform",
                "com.docker.compose.service": "platform-attachments",
            },
            "Cmd": ["python", "-m", "app.attachments.worker_runtime", "all"],
        },
    }
    runner.docker = lambda *args, **kwargs: called.append(args)

    def record(stage, **_values):
        if stage == "new_identity_owned":
            raise OSError("injected fsync failure")

    runner.record = record
    with pytest.raises(OSError):
        runner.recreate()
    assert runner.new_id == new_id
    assert called[0] == (
        "up",
        "--no-start",
        "--no-deps",
        "--force-recreate",
        "platform-attachments",
    )
    assert not any(args[0] == "start" for args in called)


@pytest.mark.parametrize("fault", ["cleanup", "permissions"])
def test_actual_migration_gate_requires_child_cleanup_and_all_six_permissions(
    tmp_path, monkeypatch, fault
):
    h = helper()
    runner = h.Hotfix(tmp_path / "inputs", tmp_path / "helpers")
    runner.validate_locks = runner.check_inputs = runner.verify_old_stopped = (
        runner.at_rest
    ) = lambda: None
    runner.record = lambda *args, **kwargs: None
    runner.baseline = [{"version": n, "sha256": "local"} for n in range(1, 96)]
    runner.child_receipt = lambda: {
        "status": "completed",
        "cleanup_verified": fault != "cleanup",
    }
    monkeypatch.setattr(h, "command", lambda *args, **kwargs: "")
    import json

    ledger = [
        *runner.baseline,
        *[
            {"version": int(name[:3]), "sha256": value}
            for name, value in h.MIGRATIONS.items()
        ],
    ]
    runner.admin = lambda sql: json.dumps(ledger) if "schema_migrations" in sql else "f"
    with pytest.raises(
        h.HotfixFailure,
        match="migration_cleanup_unverified"
        if fault == "cleanup"
        else "migration100_permissions_missing",
    ):
        runner.migrate()
