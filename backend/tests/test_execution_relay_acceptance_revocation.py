from __future__ import annotations

import json
from pathlib import Path
import re
from uuid import UUID

import pytest

from app.execution_relay import acceptance_orchestrator as subject


RUN_ID = UUID("40000000-0000-4000-8000-000000000001")
CONVERSATION_ID = UUID("40000000-0000-4000-8000-000000000002")
MESSAGE_ID = UUID("40000000-0000-4000-8000-000000000003")


def _secure_write(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o600)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    ssh_key = private / "cloud-admin-ed25519"
    cookie = private / "acceptance-session-cookie"
    _secure_write(ssh_key, b"test ssh key")
    _secure_write(cookie, b"__Host-platform_session=bounded-test-cookie")
    config = private / "acceptance-config.json"
    _secure_write(
        config,
        (json.dumps({
            "schema_version": 1,
            "cloud_admin_host": "root@47.106.112.69",
            "cloud_admin_key": str(ssh_key),
        }) + "\n").encode(),
    )
    supervisor = tmp_path / "worker-pm2.sh"
    supervisor.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    supervisor.chmod(0o700)
    return config, cookie, supervisor


class Boundary:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bytes | None, int]] = []
        self.signed_calls: list[tuple[str, str, bytes]] = []
        self.session_cookies: list[bytes] = []
        self.external_fae_calls = 0
        self.revoked = False
        self.registered = False
        self.enqueued = False
        self.fail_revoke = False
        self.pre_upload_status = 200
        self.post_lease_status = 401
        self.post_upload_status = 401
        self.session_status = 200
        self.history_status = 200
        self.fail_setup = False
        self.fail_stop = False
        self.lose_registration_response = False
        self.lose_enqueue_response = False
        self.lose_terminal_response = False
        self.fail_inspect = False
        self.fail_interrupt = False
        self.fail_restore = False
        self.job_status: str | None = None
        self.cancel_requested = False
        self.regression = self._regression()
        self.final_regression = self._regression()

    @staticmethod
    def _action(arguments: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
        marker = arguments.index("/bin/bash -s --")
        return arguments[marker + 1], arguments[marker + 2 :]

    @staticmethod
    def _regression() -> dict[str, object]:
        return {
            "schema_version": 1,
            "fae_external_domain_healthy": True,
            "fae_container_id": "a" * 64,
            "fae_image_id": "b" * 71,
            "fae_started_at": "2026-08-01T00:00:00Z",
            "fae_health": "healthy",
            "fae_https_sha256": "c" * 64,
            "platform_health_healthy": True,
            "replica_freshness": "current",
            "replica_generations": {
                "source-a": {
                    "last_sequence": 100,
                    "committed_at": "2026-08-21T00:00:00Z",
                },
            },
            "management_count": 4,
            "management_max_updated_at": "2026-08-21T00:00:00Z",
        }

    def runner(
        self,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout: int,
    ) -> subject.CommandResult:
        self.calls.append((arguments, input_bytes, timeout))
        if arguments[0].endswith("worker-pm2.sh"):
            if self.fail_stop and arguments[1] == "stop":
                return subject.CommandResult(1, b"")
            if self.fail_restore and arguments[1] == "restore":
                return subject.CommandResult(1, b"")
            if arguments[1] == "inspect":
                return subject.CommandResult(
                    0,
                    b'{"name":"orbbec-agent-execution-worker","pid":4242,'
                    b'"status":"online","pm_exec_path":"/fixed/python",'
                    b'"pm_cwd":"/fixed/backend","args":["-m","app.execution_relay.worker"]}\n',
                )
            return subject.CommandResult(0, b"")
        if arguments[0] != "/usr/bin/ssh":
            raise AssertionError(arguments)
        action, values = self._action(arguments)
        if action == "regression-probe":
            count = len([
                call for call in self.calls
                if call[0][0] == "/usr/bin/ssh" and self._action(call[0])[0] == action
            ])
            return subject.CommandResult(
                0,
                json.dumps(self.regression if count == 1 else self.final_regression).encode(),
            )
        if action == "setup":
            if self.fail_setup:
                return subject.CommandResult(1, b"")
            return subject.CommandResult(0, b'{"status":"ready"}')
        if action == "cleanup":
            return subject.CommandResult(0, b'{"status":"removed"}')
        if action == "register-disposable":
            worker_id, key_id, public_key, agents, reference = values
            assert re.fullmatch(r"relay-acceptance-[0-9a-f]{16}", worker_id)
            assert key_id == "worker-v1"
            assert len(public_key) == 43
            assert agents == "hr-bot"
            assert reference.startswith("RELAY_ACCEPT_REGISTER_")
            self.registered = True
            if self.lose_registration_response:
                return subject.CommandResult(1, b"")
            return subject.CommandResult(
                0,
                json.dumps({"status": "registered", "worker_id": worker_id}).encode(),
            )
        if action == "revoke-disposable":
            worker_id, reference = values
            assert worker_id.startswith("relay-acceptance-")
            assert reference.startswith("RELAY_ACCEPT_REVOKE_")
            if self.fail_revoke:
                return subject.CommandResult(1, b"")
            self.revoked = True
            return subject.CommandResult(
                0,
                json.dumps({"status": "revoked", "worker_id": worker_id}).encode(),
            )
        if action == "disposable-status":
            return subject.CommandResult(
                0,
                json.dumps({
                    "worker_id": values[0],
                    "status": "revoked" if self.revoked else "active",
                }).encode(),
            )
        if action == "enqueue":
            self.enqueued = True
            self.job_status = "queued"
            if self.lose_enqueue_response:
                return subject.CommandResult(1, b"")
            return subject.CommandResult(
                0,
                json.dumps({
                    "job_id": "50000000-0000-4000-8000-000000000001",
                    "run_id": values[1],
                    "status": "queued",
                }).encode(),
            )
        if action == "interrupt":
            if self.fail_interrupt:
                return subject.CommandResult(1, b"")
            if self.job_status == "queued":
                self.cancel_requested = True
                return subject.CommandResult(
                    0,
                    json.dumps({"run_id": values[0], "status": "cancel_requested"}).encode(),
                )
            self.job_status = "interrupted"
            return subject.CommandResult(
                0,
                json.dumps({"run_id": values[0], "status": "interrupted"}).encode(),
            )
        if action == "inspect":
            if self.fail_inspect:
                return subject.CommandResult(1, b"")
            status = self.job_status or "missing"
            is_terminal = status in {"completed", "failed", "cancelled", "interrupted"}
            return subject.CommandResult(
                0,
                json.dumps({
                    "run_id": values[0],
                    "agent_id": "hr-bot",
                    "status": status,
                    "event_count": 1 if is_terminal else 0,
                    "first_seq": 1 if is_terminal else None,
                    "last_seq": 1 if is_terminal else None,
                    "ordered_terminal": is_terminal,
                }).encode(),
            )
        raise AssertionError((action, values))

    def signed_request(
        self,
        worker_id: str,
        key_id: str,
        private_key: bytes,
        method: str,
        path: str,
        body: bytes,
    ) -> subject.SignedGateResponse:
        assert worker_id.startswith("relay-acceptance-")
        assert key_id == "worker-v1"
        assert len(private_key) == 32
        assert method == "POST"
        self.signed_calls.append((path, worker_id, body))
        if path.endswith("/lease"):
            status = self.post_lease_status if self.revoked else 200
            if status == 200:
                self.job_status = "leased"
            return subject.SignedGateResponse(
                status,
                {} if status == 401 else {
                    "job_id": "50000000-0000-4000-8000-000000000001",
                    "payload": {
                        "run_id": str(RUN_ID),
                        "conversation_id": str(CONVERSATION_ID),
                        "trigger_message_id": str(MESSAGE_ID),
                        "agent_id": "hr-bot",
                        "prompt": f"relay acceptance synthetic run {RUN_ID}",
                        "max_turns": 2,
                    },
                    "lease_expires_at": "2026-08-21T01:00:00Z",
                    "cancel_requested": self.cancel_requested,
                },
            )
        if path.endswith("/dispatched"):
            self.job_status = "dispatched"
            return subject.SignedGateResponse(200, {"status": "accepted"})
        if path.endswith("/events"):
            status = self.post_upload_status if self.revoked else self.pre_upload_status
            return subject.SignedGateResponse(
                status,
                {} if status == 401 else {"accepted": 1, "inserted": 1},
            )
        if path.endswith("/terminal"):
            self.job_status = json.loads(body)["status"]
            if self.lose_terminal_response:
                return subject.SignedGateResponse(500, {})
            return subject.SignedGateResponse(200, {"status": "accepted"})
        raise AssertionError(path)

    def session_probe(self, cookie: bytes) -> subject.SessionProbeResult:
        self.session_cookies.append(cookie)
        return subject.SessionProbeResult(self.session_status, self.history_status)

    def external_fae_probe(self) -> subject.ExternalFaeProbeResult:
        self.external_fae_calls += 1
        return subject.ExternalFaeProbeResult(200, "c" * 64)


def _run(
    config: Path,
    cookie: Path,
    supervisor: Path,
    boundary: Boundary,
) -> subject.FinalGateResult:
    values = iter((RUN_ID, CONVERSATION_ID, MESSAGE_ID))
    return subject.run_gates_09_to_10(
        config,
        runner=boundary.runner,
        signed_requester=boundary.signed_request,
        session_probe=boundary.session_probe,
        external_fae_probe=boundary.external_fae_probe,
        token_factory=lambda: "0123456789abcdef",
        disposable_key_factory=lambda: b"K" * 32,
        uuid_factory=lambda: next(values),
        private_root=config.parent,
        session_cookie_path=cookie,
        worker_supervisor_path=supervisor,
        current_user="agentops",
        uid=501,
    )


def test_gate_09_uses_unique_worker_real_signing_revokes_and_keeps_sessions(tmp_path: Path) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    boundary = Boundary()
    result = _run(config, cookie, launchagent, boundary)

    assert result.disposable_worker_id == "relay-acceptance-0123456789abcdef"
    assert result.lease_status == 401
    assert result.upload_status == 401
    assert result.sessions_status == 200
    assert result.history_status == 200
    assert boundary.registered is True and boundary.revoked is True
    paths = [path for path, _worker, _body in boundary.signed_calls]
    assert paths == [
        "/api/v1/execution-worker/lease",
        f"/api/v1/execution-worker/runs/{RUN_ID}/dispatched",
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
        f"/api/v1/execution-worker/runs/{RUN_ID}/terminal",
        "/api/v1/execution-worker/lease",
        f"/api/v1/execution-worker/runs/{RUN_ID}/events",
    ]
    assert json.loads(boundary.signed_calls[0][2]) == {
        "acceptance_run_id": str(RUN_ID)
    }
    assert boundary.session_cookies == [b"__Host-platform_session=bounded-test-cookie"]
    assert boundary.external_fae_calls == 1
    maintenance = [
        boundary._action(call[0])
        for call in boundary.calls
        if call[0][0] == "/usr/bin/ssh"
        and boundary._action(call[0])[0] in {"register-disposable", "revoke-disposable"}
    ]
    assert all("agentops-mac-primary" not in values for _action, values in maintenance)
    assert all("worker-v2" not in values for _action, values in maintenance)


def test_gate_09_requires_pre_revoke_success_post_revoke_401_and_cookie_200(tmp_path: Path) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    boundary = Boundary()
    boundary.pre_upload_status = 401
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.revoked is True

    boundary = Boundary()
    boundary.post_lease_status = 200
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.revoked is True

    boundary = Boundary()
    boundary.post_upload_status = 200
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)

    boundary = Boundary()
    boundary.history_status = 401
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)


def test_gate_09_rejects_open_cookie_and_noncanonical_disposable_token(tmp_path: Path) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    cookie.chmod(0o644)
    boundary = Boundary()
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)

    cookie.chmod(0o600)
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        subject.run_gates_09_to_10(
            config,
            runner=boundary.runner,
            signed_requester=boundary.signed_request,
            session_probe=boundary.session_probe,
            token_factory=lambda: "NOT-HEX-OR-BOUNDED",
            disposable_key_factory=lambda: b"K" * 32,
            uuid_factory=lambda: RUN_ID,
            private_root=config.parent,
            session_cookie_path=cookie,
            worker_supervisor_path=launchagent,
            current_user="agentops",
            uid=501,
        )


def test_gate_10_allows_healthy_replica_generation_to_advance(tmp_path: Path) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    boundary = Boundary()
    boundary.final_regression["replica_generations"] = {
        "source-a": {
            "last_sequence": 101,
            "committed_at": "2026-08-21T00:01:00Z",
        },
    }
    boundary.final_regression["management_count"] = 5
    boundary.final_regression["management_max_updated_at"] = "2026-08-21T00:01:00Z"
    _run(config, cookie, launchagent, boundary)


@pytest.mark.parametrize(
    "management_count,management_max_updated_at",
    (
        (3, "2026-08-21T00:01:00Z"),
        (4, "2026-08-20T23:59:00Z"),
        (0, None),
    ),
)
def test_gate_10_rejects_management_replica_regression(
    tmp_path: Path,
    management_count: int,
    management_max_updated_at: str | None,
) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    boundary = Boundary()
    boundary.final_regression["management_count"] = management_count
    boundary.final_regression["management_max_updated_at"] = management_max_updated_at
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.revoked is True


def test_gate_10_requires_exact_fae_and_monotonic_current_replica(tmp_path: Path) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    boundary = Boundary()
    boundary.final_regression["fae_started_at"] = "2026-08-21T00:00:00Z"
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.revoked is True

    mutations = (
        {"replica_freshness": "stale"},
        {"replica_generations": {
            "source-a": {
                "last_sequence": 99,
                "committed_at": "2026-08-21T00:01:00Z",
            },
        }},
        {"replica_generations": {}},
    )
    for mutation in mutations:
        boundary = Boundary()
        boundary.final_regression.update(mutation)
        with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
            _run(config, cookie, launchagent, boundary)
        assert boundary.revoked is True


def test_unrevoked_disposable_is_audited_revoked_and_cleanup_failure_wins(tmp_path: Path) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    boundary = Boundary()
    boundary.pre_upload_status = 500
    boundary.fail_revoke = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance cleanup failed"):
        _run(config, cookie, launchagent, boundary)
    actions = [
        boundary._action(call[0])[0]
        for call in boundary.calls
        if call[0][0] == "/usr/bin/ssh"
    ]
    assert "register-disposable" in actions
    assert "revoke-disposable" in actions
    worker = [
        call[0][1]
        for call in boundary.calls
        if call[0][0].endswith("worker-pm2.sh")
    ]
    assert worker[-1] == "restore"


def test_partial_setup_failed_bootout_and_lost_registration_are_compensated(
    tmp_path: Path,
) -> None:
    config, cookie, launchagent = _fixture(tmp_path)

    boundary = Boundary()
    boundary.fail_setup = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert "cleanup" in [
        boundary._action(call[0])[0]
        for call in boundary.calls if call[0][0] == "/usr/bin/ssh"
    ]

    boundary = Boundary()
    boundary.fail_stop = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    worker = [
        call[0][1]
        for call in boundary.calls
        if call[0][0].endswith("worker-pm2.sh")
    ]
    assert worker[-1] == "restore"

    boundary = Boundary()
    boundary.lose_registration_response = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.registered is True and boundary.revoked is True


def test_enqueue_and_terminal_response_loss_prove_terminal_before_revoke(
    tmp_path: Path,
) -> None:
    config, cookie, launchagent = _fixture(tmp_path)

    boundary = Boundary()
    boundary.lose_enqueue_response = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.job_status == "cancelled"
    assert boundary.revoked is True
    paths = [path for path, _worker, _body in boundary.signed_calls]
    assert paths == [
        "/api/v1/execution-worker/lease",
        f"/api/v1/execution-worker/runs/{RUN_ID}/terminal",
    ]
    assert json.loads(boundary.signed_calls[0][2]) == {
        "acceptance_run_id": str(RUN_ID)
    }

    boundary = Boundary()
    boundary.lose_terminal_response = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance gate failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.job_status == "interrupted"
    assert boundary.revoked is True


def test_terminal_proof_failure_still_revokes_after_attempt_and_restore_failure_wins(
    tmp_path: Path,
) -> None:
    config, cookie, launchagent = _fixture(tmp_path)
    boundary = Boundary()
    boundary.pre_upload_status = 500
    boundary.fail_inspect = True
    boundary.fail_interrupt = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance cleanup failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.revoked is True
    actions = [
        boundary._action(call[0])[0]
        for call in boundary.calls if call[0][0] == "/usr/bin/ssh"
    ]
    assert actions.index("revoke-disposable") > actions.index("interrupt")

    boundary = Boundary()
    boundary.lose_enqueue_response = True
    boundary.fail_restore = True
    with pytest.raises(subject.AcceptanceGateError, match="acceptance cleanup failed"):
        _run(config, cookie, launchagent, boundary)
    assert boundary.job_status == "cancelled"
    assert boundary.revoked is True


def test_final_remote_script_uses_private_registration_directory_and_real_replica_probe() -> None:
    source = subject._final_remote_script().decode()
    assert '/usr/bin/install -d -o root -g root -m 700 "$registration_root"' in source
    assert 'chmod 600 "$document"' in source
    assert '-v "$registration_root:/run/worker-registration:ro"' in source
    assert "https://agent.orbbec.com.cn/api/health" in source
    assert "platform_replica.generations" in source
    assert "platform_replica.management_projections" in source
    assert "/api/deployment" not in source


def test_main_runs_disposable_before_production_and_finishes_with_invariants(monkeypatch) -> None:
    calls: list[str] = []
    initial = subject.InitialGateResult("agentops-mac-primary", "worker-v2", "a" * 64, 0)
    execution = subject.ExecutionGateResult(
        RUN_ID, RUN_ID, RUN_ID, RUN_ID, 0
    )
    final = subject.FinalGateResult(
        "relay-acceptance-0123456789abcdef", 401, 401, 200, 200
    )

    monkeypatch.setenv("USER", "agentops")
    monkeypatch.setattr(
        subject, "run_gates_09_to_10",
        lambda *_args, **_kwargs: calls.append("09-10") or final,
    )
    monkeypatch.setattr(
        subject, "run_gates_04_to_08",
        lambda *_args, **_kwargs: calls.append("04-08") or execution,
    )
    monkeypatch.setattr(
        subject, "run_gates_01_to_03",
        lambda *_args, **_kwargs: calls.append("01-03") or initial,
    )

    assert subject.main(["/private/acceptance-config.json"]) == 0
    assert calls == ["01-03", "09-10", "04-08", "01-03"]
