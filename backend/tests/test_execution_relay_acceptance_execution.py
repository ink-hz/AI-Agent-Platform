from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
from uuid import UUID

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.execution_relay import acceptance_orchestrator as subject
from app.execution_relay.models import RelayEvent


RUNS = (
    UUID("10000000-0000-4000-8000-000000000001"),
    UUID("10000000-0000-4000-8000-000000000002"),
    UUID("10000000-0000-4000-8000-000000000003"),
    UUID("10000000-0000-4000-8000-000000000004"),
)
EXTRAS = tuple(
    UUID(f"20000000-0000-4000-8000-{index:012d}") for index in range(1, 9)
)


def _secure_write(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o600)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    ssh_key = private / "cloud-admin-ed25519"
    worker_key = private / "execution-worker-ed25519.key"
    dsn = private / "execution-worker-postgres-dsn"
    token = private / "metabot-api-token"
    _secure_write(ssh_key, b"test ssh key")
    _secure_write(worker_key, b"W" * 32)
    public_key = Ed25519PrivateKey.from_private_bytes(b"W" * 32).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    _secure_write(
        private / "execution-worker-public.json",
        (json.dumps({
            "worker_id": "agentops-mac-primary",
            "key_id": "worker-v2",
            "public_key_base64url": base64.urlsafe_b64encode(public_key).decode().rstrip("="),
            "allowed_agent_ids": [
                "hr-bot",
                "fae-bot",
                "marketing-prospecting-bot",
                "marketing-inbound-bot",
                "marketing-voice-bot",
                "marketing-intelligence-bot",
                "marketing-gtm-bot",
                "agent-brain-bot",
            ],
        }) + "\n").encode(),
    )
    _secure_write(dsn, b"postgresql://runtime:test@127.0.0.1:5432/worker")
    _secure_write(token, b"test metabot token")
    backend = tmp_path / "backend"
    python = backend / ".venv/bin/python"
    python.parent.mkdir(parents=True, mode=0o700)
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o700)
    contract = tmp_path / "runtime-contract.json"
    _secure_write(contract, b'{"bots":[]}\n')
    supervisor = tmp_path / "worker-pm2.sh"
    supervisor.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    supervisor.chmod(0o700)
    config = private / "acceptance-config.json"
    _secure_write(
        config,
        (json.dumps({
            "schema_version": 1,
            "cloud_admin_host": "root@47.106.112.69",
            "cloud_admin_key": str(ssh_key),
        }) + "\n").encode(),
    )
    return config, worker_key, dsn


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.waits: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: int) -> int:
        self.waits.append(timeout)
        if self.returncode is None:
            raise TimeoutError
        return self.returncode


class Boundary:
    def __init__(self, private: Path) -> None:
        self.private = private
        self.calls: list[tuple[tuple[str, ...], bytes | None, int]] = []
        self.process_calls: list[dict[str, object]] = []
        self.processes: list[FakeProcess] = []
        self.signals: list[tuple[int, signal.Signals]] = []
        self.enqueued: list[tuple[str, UUID]] = []
        self.inspect_overrides: dict[UUID, dict[str, object]] = {}
        self.fail_remote_cleanup = False
        self.fail_restore = False
        self.replay_inserted = 0
        self.replayed: list[tuple[UUID, tuple[RelayEvent, ...]]] = []
        self.replay_key_ids: list[str] = []
        self.local_reads: dict[UUID, int] = {}

    @staticmethod
    def _remote_action(arguments: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
        marker = arguments.index("/bin/bash -s --")
        return arguments[marker + 1], arguments[marker + 2 :]

    def runner(
        self,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout: int,
    ) -> subject.CommandResult:
        self.calls.append((arguments, input_bytes, timeout))
        if arguments[0].endswith("worker-pm2.sh"):
            if arguments[1] == "restore" and self.fail_restore:
                return subject.CommandResult(1, b"")
            if arguments[1] == "inspect":
                return subject.CommandResult(
                    0,
                    b'{"name":"orbbec-agent-execution-worker","pid":4242,'
                    b'"status":"online","pm_exec_path":"/fixed/python",'
                    b'"pm_cwd":"/fixed/backend","args":["-m","app.execution_relay.worker"]}\n',
                )
            return subject.CommandResult(0, b"")
        if arguments[0] == "/usr/sbin/lsof":
            pid = arguments[arguments.index("-p") + 1]
            return subject.CommandResult(
                0,
                f"python {pid} agentops 7u IPv4 TCP 127.0.0.1:9120 (LISTEN)\n".encode(),
            )
        if arguments[0] != "/usr/bin/ssh":
            raise AssertionError(arguments)
        action, values = self._remote_action(arguments)
        if action == "setup":
            return subject.CommandResult(0, b'{"status":"ready"}\n')
        if action == "cleanup":
            return subject.CommandResult(
                1 if self.fail_remote_cleanup else 0,
                b"" if self.fail_remote_cleanup else b'{"status":"removed"}\n',
            )
        if action == "enqueue":
            agent_id, run_value, _conversation, _message = values
            run_id = UUID(run_value)
            self.enqueued.append((agent_id, run_id))
            if len(self.enqueued) == 3:
                _secure_write(self.private / "execution-relay-acceptance" / "completion-paused", run_value.encode())
            if len(self.enqueued) == 4:
                _secure_write(self.private / "execution-relay-acceptance" / "dispatching-paused", run_value.encode())
                state_path = self.private / "execution-relay-acceptance" / "state.json"
                state = json.loads(state_path.read_text())
                state["metabot_posts"] = {run_value: 1}
                _secure_write(state_path, json.dumps(state).encode())
            return subject.CommandResult(
                0,
                json.dumps({
                    "job_id": f"30000000-0000-4000-8000-{len(self.enqueued):012d}",
                    "run_id": run_value,
                    "status": "queued",
                }).encode(),
            )
        if action == "inspect":
            run_id = UUID(values[0])
            overridden = self.inspect_overrides.get(run_id)
            if overridden is not None:
                return subject.CommandResult(0, json.dumps(overridden).encode())
            agent = next(
                agent for agent, selected in self.enqueued if selected == run_id
            )
            dispatch = run_id == RUNS[3]
            return subject.CommandResult(
                0,
                json.dumps({
                    "run_id": str(run_id),
                    "agent_id": agent,
                    "status": "interrupted" if dispatch else "completed",
                    "event_count": 0 if dispatch else 2,
                    "first_seq": None if dispatch else 1,
                    "last_seq": None if dispatch else 2,
                    "ordered_terminal": not dispatch,
                }).encode(),
            )
        if action == "interrupt":
            return subject.CommandResult(
                0,
                json.dumps({"run_id": values[0], "status": "interrupted"}).encode(),
            )
        raise AssertionError((action, values))

    def process_factory(self, **values: object) -> FakeProcess:
        self.process_calls.append(values)
        process = FakeProcess(5001 + len(self.processes))
        self.processes.append(process)
        return process

    def kill_process(self, pid: int, selected_signal: signal.Signals) -> None:
        self.signals.append((pid, selected_signal))
        process = next(item for item in self.processes if item.pid == pid)
        process.returncode = -int(selected_signal)

    def local_state(self, _dsn: str, run_id: UUID) -> subject.LocalRunState:
        count = self.local_reads.get(run_id, 0)
        self.local_reads[run_id] = count + 1
        if run_id == RUNS[2]:
            return subject.LocalRunState(
                state="completed",
                event_count=2,
                first_seq=1,
                last_seq=2,
                undelivered_count=2 if count == 0 else 0,
            )
        if run_id == RUNS[3]:
            return subject.LocalRunState(
                state="dispatching" if count == 0 else "interrupted",
                event_count=0,
                first_seq=None,
                last_seq=None,
                undelivered_count=0,
            )
        return subject.LocalRunState("completed", 2, 1, 2, 0)

    def local_events(self, _dsn: str, run_id: UUID) -> tuple[RelayEvent, ...]:
        return (
            RelayEvent(
                run_id=run_id,
                seq=1,
                event_type="turn",
                created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                payload={"text": "synthetic"},
            ),
            RelayEvent(
                run_id=run_id,
                seq=2,
                event_type="run.completed",
                created_at=datetime(2026, 8, 21, 0, 0, 1, tzinfo=timezone.utc),
                payload={"status": "completed"},
            ),
        )

    def duplicate_reupload(
        self,
        run_id: UUID,
        events: tuple[RelayEvent, ...],
        key_id: str,
        _private_key: bytes,
    ) -> subject.DuplicateUploadResult:
        self.replayed.append((run_id, events))
        self.replay_key_ids.append(key_id)
        return subject.DuplicateUploadResult(
            status_code=200,
            accepted=len(events),
            inserted=self.replay_inserted,
        )


def _run(
    config: Path,
    worker_key: Path,
    dsn: Path,
    boundary: Boundary,
    *,
    uuid_factory: Callable[[], UUID] | None = None,
) -> subject.ExecutionGateResult:
    values = iter((*RUNS, *EXTRAS))
    return subject.run_gates_04_to_08(
        config,
        runner=boundary.runner,
        process_factory=boundary.process_factory,
        kill_process=boundary.kill_process,
        local_state_reader=boundary.local_state,
        local_events_reader=boundary.local_events,
        duplicate_uploader=boundary.duplicate_reupload,
        sleep=lambda _seconds: None,
        uuid_factory=uuid_factory or (lambda: next(values)),
        private_root=config.parent,
        worker_private_key_path=worker_key,
        worker_public_document_path=config.parent / "execution-worker-public.json",
        runtime_dsn_path=dsn,
        hook_directory=config.parent / "execution-relay-acceptance",
        backend_root=config.parent.parent / "backend",
        worker_supervisor_path=config.parent.parent / "worker-pm2.sh",
        metabot_contract_path=config.parent.parent / "runtime-contract.json",
        metabot_token_path=config.parent / "metabot-api-token",
        current_user="agentops",
        uid=501,
    )


def test_gate_04_to_08_runs_real_cli_crashes_exact_children_and_restores(tmp_path: Path) -> None:
    config, worker_key, dsn = _fixture(tmp_path)
    boundary = Boundary(config.parent)
    result = _run(config, worker_key, dsn, boundary)

    assert result.hr_run_id == RUNS[0]
    assert result.marketing_intelligence_run_id == RUNS[1]
    assert result.completion_crash_run_id == RUNS[2]
    assert result.dispatching_crash_run_id == RUNS[3]
    assert result.duplicate_dispatches == 0
    assert boundary.enqueued == [
        ("hr-bot", RUNS[0]),
        ("marketing-intelligence-bot", RUNS[1]),
        ("hr-bot", RUNS[2]),
        ("marketing-intelligence-bot", RUNS[3]),
    ]
    assert [(pid, selected) for pid, selected in boundary.signals if selected == signal.SIGKILL] == [
        (5001, signal.SIGKILL),
        (5002, signal.SIGKILL),
    ]
    assert len(boundary.process_calls) == 3
    controls = {
        call["environment"]["PLATFORM_WORKER_ACCEPTANCE_CONTROL_FILE"]
        for call in boundary.process_calls
    }
    assert controls == {str(config.parent / "execution-relay-acceptance/control.json")}
    assert {
        call["environment"]["PLATFORM_WORKER_KEY_ID"]
        for call in boundary.process_calls
    } == {"worker-v2"}
    platform_environment = {
        key
        for key in boundary.process_calls[0]["environment"]
        if key.startswith("PLATFORM_")
    }
    assert platform_environment == {
        "PLATFORM_WORKER_ID",
        "PLATFORM_WORKER_KEY_ID",
        "PLATFORM_WORKER_PRIVATE_KEY_FILE",
        "PLATFORM_WORKER_DATABASE_URL_FILE",
        "PLATFORM_WORKER_CALLBACK_PORT",
        "PLATFORM_WORKER_CLOUD_URL",
        "PLATFORM_METABOT_RUNTIME_CONTRACT",
        "PLATFORM_METABOT_API_SECRET_FILE",
        "PLATFORM_WORKER_ACCEPTANCE_HOOKS",
        "PLATFORM_WORKER_ACCEPTANCE_CONTROL_FILE",
    }
    assert boundary.replayed[0][0] == RUNS[0]
    assert boundary.replay_key_ids == ["worker-v2"]
    assert [event.seq for event in boundary.replayed[0][1]] == [1, 2]
    worker_actions = [
        call[0][1]
        for call in boundary.calls
        if call[0][0].endswith("worker-pm2.sh")
    ]
    assert worker_actions == ["inspect", "stop", "restore"]
    remote_actions = [
        boundary._remote_action(call[0])[0]
        for call in boundary.calls
        if call[0][0] == "/usr/bin/ssh"
    ]
    assert remote_actions[0] == "setup"
    assert remote_actions[-1] == "cleanup"


def test_gate_04_to_08_rejects_non_executable_supervisor_before_stop(
    tmp_path: Path,
) -> None:
    config, worker_key, dsn = _fixture(tmp_path)
    supervisor = config.parent.parent / "worker-pm2.sh"
    supervisor.chmod(0o600)
    boundary = Boundary(config.parent)

    with pytest.raises(ValueError, match="acceptance gate failed"):
        _run(config, worker_key, dsn, boundary)

    assert not any(
        call[0][0].endswith("worker-pm2.sh") and call[0][1] == "stop"
        for call in boundary.calls
    )


def test_gate_06_requires_exact_duplicate_inserted_zero(tmp_path: Path) -> None:
    config, worker_key, dsn = _fixture(tmp_path)
    boundary = Boundary(config.parent)
    boundary.replay_inserted = 1
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, worker_key, dsn, boundary)
    worker_actions = [
        call[0][1]
        for call in boundary.calls
        if call[0][0].endswith("worker-pm2.sh")
    ]
    assert "stop" not in worker_actions and "restore" not in worker_actions


@pytest.mark.parametrize(
    "evidence",
    (
        {"status": "failed", "event_count": 2, "ordered_terminal": True},
        {"status": "completed", "event_count": 1, "ordered_terminal": True},
        {"status": "completed", "event_count": 2, "ordered_terminal": False},
    ),
)
def test_gate_04_and_05_require_completed_multi_event_ordered_terminal(
    tmp_path: Path, evidence: dict[str, object]
) -> None:
    config, worker_key, dsn = _fixture(tmp_path)
    boundary = Boundary(config.parent)
    boundary.inspect_overrides[RUNS[0]] = {
        "run_id": str(RUNS[0]),
        "agent_id": "hr-bot",
        "first_seq": 1,
        "last_seq": evidence["event_count"],
        **evidence,
    }
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, worker_key, dsn, boundary)


def test_gate_07_requires_terminal_contiguous_retained_outbox(tmp_path: Path) -> None:
    config, worker_key, dsn = _fixture(tmp_path)
    boundary = Boundary(config.parent)
    original = boundary.local_state

    def missing(dsn_value: str, run_id: UUID) -> subject.LocalRunState:
        value = original(dsn_value, run_id)
        if run_id == RUNS[2]:
            return subject.LocalRunState("completed", 2, 1, 2, 0)
        return value

    boundary.local_state = missing  # type: ignore[method-assign]
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, worker_key, dsn, boundary)


def test_gate_08_requires_persistent_single_post_and_interrupted_cloud(tmp_path: Path) -> None:
    config, worker_key, dsn = _fixture(tmp_path)
    boundary = Boundary(config.parent)
    original_runner = boundary.runner

    def bad_runner(arguments, *, input_bytes=None, timeout):
        result = original_runner(arguments, input_bytes=input_bytes, timeout=timeout)
        if arguments[0] == "/usr/bin/ssh":
            action, values = boundary._remote_action(arguments)
            if action == "enqueue" and len(boundary.enqueued) == 4:
                state_path = config.parent / "execution-relay-acceptance/state.json"
                state = json.loads(state_path.read_text())
                state["metabot_posts"] = {values[1]: 2}
                _secure_write(state_path, json.dumps(state).encode())
        return result

    boundary.runner = bad_runner  # type: ignore[method-assign]
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, worker_key, dsn, boundary)

    boundary = Boundary(config.parent)
    boundary.inspect_overrides[RUNS[3]] = {
        "run_id": str(RUNS[3]),
        "agent_id": "marketing-intelligence-bot",
        "status": "completed",
        "event_count": 1,
        "first_seq": 1,
        "last_seq": 1,
        "ordered_terminal": True,
    }
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, worker_key, dsn, boundary)


def test_cleanup_failure_overrides_success_and_still_attempts_pm2_restore(tmp_path: Path) -> None:
    config, worker_key, dsn = _fixture(tmp_path)
    boundary = Boundary(config.parent)
    boundary.fail_remote_cleanup = True
    boundary.fail_restore = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance cleanup failed"):
        _run(config, worker_key, dsn, boundary)
    worker_actions = [
        call[0][1]
        for call in boundary.calls
        if call[0][0].endswith("worker-pm2.sh")
    ]
    assert "stop" in worker_actions and "restore" in worker_actions
