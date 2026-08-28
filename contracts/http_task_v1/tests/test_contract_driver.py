from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID, uuid5

import httpx
import pytest
from pydantic import ValidationError

CONTRACT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONTRACT_ROOT))


def _runner_module():
    from orbbec_task_contract import runner

    return runner


class ContractTarget:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.create_keys: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.message_keys: dict[tuple[str, str], dict[str, Any]] = {}
        self.cancel_responses: dict[str, dict[str, Any]] = {}
        self.execute_keys: dict[tuple[str, str], dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer task-token"
        assert request.headers["x-orbbec-task-contract"] == "orbbec-http-task/v1"
        return self._dispatch(request)

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/capabilities"):
            return self._json(
                200,
                {
                    "contract_version": "orbbec-http-task/v1",
                    "capability_version": 2,
                    "supports_actions": True,
                },
            )
        if request.method == "POST" and path.endswith("/tasks"):
            return self._create(request)
        if request.method == "POST" and path.endswith("/messages"):
            return self._message(request)
        if request.method == "POST" and path.endswith("/cancel"):
            return self._cancel(request)
        if request.method == "GET" and path.endswith("/events"):
            return self._events(request)
        if request.method == "GET" and "/tasks/" in path:
            return self._task(request)
        if request.method == "POST" and path.endswith("/execute"):
            return self._execute(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    @staticmethod
    def _json(status: int, document: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            status, json=document, headers={"content-type": "application/json"}
        )

    @staticmethod
    def _body(request: httpx.Request) -> dict[str, Any]:
        return json.loads(request.content)

    def _create(self, request: httpx.Request) -> httpx.Response:
        body = self._body(request)
        assert body["contract_version"] == "orbbec-http-task/v1"
        if body["capability_version"] != 2:
            return self._json(
                409,
                {
                    "error": {
                        "code": "capability_changed",
                        "current_capability_version": 2,
                        "must_refresh_capabilities": True,
                    }
                },
            )
        if datetime.fromisoformat(
            body["deadline_at"].replace("Z", "+00:00")
        ) <= datetime.now(UTC):
            return self._json(409, {"error": {"code": "deadline_expired"}})
        key = body["idempotency_key"]
        existing = self.create_keys.get(key)
        if existing is not None:
            original, response = existing
            if body != original:
                return self._json(409, {"error": {"code": "idempotency_conflict"}})
            return self._json(202, {**response, "duplicate": True})
        task_id = body["platform_task_id"]
        response = {
            "contract_version": "orbbec-http-task/v1",
            "downstream_task_id": f"downstream-{len(self.tasks) + 1}",
            "status": "queued",
            "next_event_seq": 1,
            "duplicate": False,
        }
        self.tasks[task_id] = body
        self.create_keys[key] = (body, response)
        return self._json(202, response)

    def _message(self, request: httpx.Request) -> httpx.Response:
        task_id = request.url.path.split("/tasks/", 1)[1].split("/", 1)[0]
        body = self._body(request)
        assert set(body) == {
            "contract_version",
            "message_seq",
            "content",
            "attachment_refs",
            "idempotency_key",
        }
        key = (task_id, body["idempotency_key"])
        existing = self.message_keys.get(key)
        if existing is not None:
            if body != existing:
                return self._json(409, {"error": {"code": "message_sequence_conflict"}})
            return self._json(
                202,
                {
                    "status": "accepted",
                    "message_seq": body["message_seq"],
                    "duplicate": True,
                },
            )
        self.message_keys[key] = body
        return self._json(
            202,
            {
                "status": "accepted",
                "message_seq": body["message_seq"],
                "duplicate": False,
            },
        )

    def _cancel(self, request: httpx.Request) -> httpx.Response:
        task_id = request.url.path.split("/tasks/", 1)[1].split("/", 1)[0]
        response = self.cancel_responses.setdefault(
            task_id,
            {
                "contract_version": "orbbec-http-task/v1",
                "status": "cancelled",
                "cancel_requested": True,
            },
        )
        return self._json(202, response)

    def _events(self, request: httpx.Request) -> httpx.Response:
        task_id = request.url.path.split("/tasks/", 1)[1].split("/", 1)[0]
        task = self.tasks[task_id]
        query = parse_qs(request.url.query.decode())
        assert query["wait_seconds"] == ["0"]
        after = int(query["after"][0])
        limit = int(query["limit"][0])
        assert 1 <= limit <= 100
        turn_ref = task["turn_ref"]
        if turn_ref == "contract:event-sequence-terminal":
            events = [
                {
                    "seq": 1,
                    "kind": "work_update",
                    "created_at": "2026-08-27T10:00:00Z",
                    "payload": {"phase": "started"},
                },
                {
                    "seq": 2,
                    "kind": "message",
                    "created_at": "2026-08-27T10:00:01Z",
                    "payload": {"content": "still working"},
                },
                {
                    "seq": 3,
                    "kind": "result",
                    "created_at": "2026-08-27T10:00:02Z",
                    "payload": {"outcome": "completed"},
                },
            ]
        elif turn_ref == "contract:cancel":
            events = [
                {
                    "seq": 1,
                    "kind": "cancelled",
                    "created_at": "2026-08-27T10:00:00Z",
                    "payload": {"reason_code": "cancel_requested"},
                }
            ]
        elif turn_ref == "contract:deadline":
            deadline = datetime.fromisoformat(
                task["deadline_at"].replace("Z", "+00:00")
            )
            now = datetime.now(UTC)
            events = (
                [
                    {
                        "seq": 1,
                        "kind": "timeout",
                        "created_at": now.isoformat().replace("+00:00", "Z"),
                        "payload": {"reason_code": "deadline_expired"},
                    }
                ]
                if now >= deadline
                else []
            )
        elif turn_ref == "contract:action":
            platform_task_id = UUID(task_id)
            action_id = uuid5(platform_task_id, "action:1")
            from orbbec_task_contract.models import ActionDigestInput, action_digest

            digest = action_digest(
                ActionDigestInput(
                    platform_task_id=platform_task_id,
                    action_seq=1,
                    action_kind="voc.submit",
                    parameters={"priority": 2, "title": "机器人客户反馈"},
                )
            )
            events = [
                {
                    "seq": 1,
                    "kind": "action_required",
                    "created_at": "2026-08-27T10:00:00Z",
                    "payload": {
                        "action_id": str(action_id),
                        "action_seq": 1,
                        "action_kind": "voc.submit",
                        "summary": "提交本次 VOC 草稿",
                        "impact": "将生成正式业务记录",
                        "parameters": {"priority": 2, "title": "机器人客户反馈"},
                        "action_digest": digest,
                        "expires_at": "2030-08-27T12:00:00Z",
                        "execution_timeout_seconds": 120,
                    },
                }
            ]
        else:
            events = []
        page = events[after : after + limit]
        next_after = page[-1]["seq"] if page else after
        terminal = (
            bool(events)
            and turn_ref
            in {
                "contract:event-sequence-terminal",
                "contract:cancel",
                "contract:deadline",
            }
            and (next_after >= len(events))
        )
        return self._json(
            200,
            {
                "contract_version": "orbbec-http-task/v1",
                "downstream_task_id": f"downstream-{list(self.tasks).index(task_id) + 1}",
                "events": page,
                "next_after": next_after,
                "terminal": terminal,
            },
        )

    def _task(self, request: httpx.Request) -> httpx.Response:
        task_id = request.url.path.split("/tasks/", 1)[1]
        turn_ref = self.tasks[task_id]["turn_ref"]
        statuses = {
            "contract:event-sequence-terminal": "completed",
            "contract:cancel": "cancelled",
            "contract:deadline": "timed_out",
        }
        return self._json(
            200,
            {
                "contract_version": "orbbec-http-task/v1",
                "platform_task_id": task_id,
                "status": statuses.get(turn_ref, "queued"),
            },
        )

    def _execute(self, request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["read"] >= 900
        task_id = request.url.path.split("/tasks/", 1)[1].split("/", 1)[0]
        action_id = request.url.path.split("/actions/", 1)[1].split("/", 1)[0]
        body = self._body(request)
        assert set(body) == {"action_id", "action_digest", "idempotency_key"}
        assert body["action_id"] == action_id
        platform_task_id = UUID(task_id)
        expected_action_id = str(uuid5(platform_task_id, "action:1"))
        assert action_id == expected_action_id
        from orbbec_task_contract.models import ActionDigestInput, action_digest

        expected_digest = action_digest(
            ActionDigestInput(
                platform_task_id=platform_task_id,
                action_seq=1,
                action_kind="voc.submit",
                parameters={"priority": 2, "title": "机器人客户反馈"},
            )
        )
        if body["action_digest"] != expected_digest:
            return self._json(409, {"error": {"code": "action_digest_mismatch"}})
        key = (action_id, body["idempotency_key"])
        response = self.execute_keys.setdefault(
            key,
            {
                "contract_version": "orbbec-http-task/v1",
                "status": "completed",
                "result": {"business_record_id": "record-1"},
            },
        )
        return self._json(200, response)


def test_runner_executes_the_complete_http_contract() -> None:
    runner = _runner_module()
    target = ContractTarget()

    report = runner.ContractRunner(
        base_url="https://agent.example",
        token_broker=StaticTokenBroker(),
        agent_id="ai-fae-agent",
        authorized_scopes=("fae.answer",),
        transport=httpx.MockTransport(target),
    ).run()

    assert report.executed_cases == (
        "create_idempotency_capability",
        "finite_event_pages_sequence_terminal",
        "follow_up",
        "cancel",
        "deadline",
        "action_proposal_execute",
    )
    assert any(request.url.path.endswith("/messages") for request in target.requests)
    assert any(request.url.path.endswith("/cancel") for request in target.requests)
    assert any(request.url.path.endswith("/execute") for request in target.requests)
    cancel_task_id = next(
        task_id
        for task_id, task in target.tasks.items()
        if task["turn_ref"] == "contract:cancel"
    )
    assert any(
        request.url.path.endswith(f"/tasks/{cancel_task_id}/events")
        for request in target.requests
    )


def test_nonblocking_limit_does_not_apply_to_action_execution() -> None:
    runner = _runner_module()

    class SlowActionTarget(ContractTarget):
        def _execute(self, request: httpx.Request) -> httpx.Response:
            sleep(0.05)
            return super()._execute(request)

    report = runner.ContractRunner(
        base_url="https://agent.example",
        token_broker=StaticTokenBroker(),
        agent_id="ai-fae-agent",
        authorized_scopes=("fae.answer",),
        transport=httpx.MockTransport(SlowActionTarget()),
        max_request_seconds=0.01,
    ).run()

    assert "action_proposal_execute" in report.executed_cases


def test_runner_rejects_python_310() -> None:
    runner = _runner_module()

    with pytest.raises(runner.ContractViolation, match="Python 3.11"):
        runner.require_supported_python((3, 10, 14))


@pytest.mark.parametrize(
    ("document", "message"),
    [
            (
                {
                    "contract_version": "orbbec-http-task/v1",
                    "downstream_task_id": "downstream-1",
                    "events": [
                    {
                        "seq": 2,
                        "kind": "message",
                        "created_at": "2026-08-27T10:00:00Z",
                        "payload": {},
                    }
                ],
                "next_after": 2,
                "terminal": False,
            },
            "strictly continuous",
        ),
            (
                {
                    "contract_version": "orbbec-http-task/v1",
                    "downstream_task_id": "downstream-1",
                    "events": [
                    {
                        "seq": 1,
                        "kind": "timed_out",
                        "created_at": "2026-08-27T10:00:00Z",
                        "payload": {},
                    }
                ],
                "next_after": 1,
                "terminal": True,
            },
            "event kind",
        ),
            (
                {
                    "contract_version": "orbbec-http-task/v1",
                    "downstream_task_id": "downstream-1",
                    "events": [
                    {
                        "seq": 1,
                        "kind": "result",
                        "created_at": "2026-08-27T10:00:00Z",
                        "payload": {},
                    },
                    {
                        "seq": 2,
                        "kind": "message",
                        "created_at": "2026-08-27T10:00:01Z",
                        "payload": {},
                    },
                ],
                "next_after": 2,
                "terminal": True,
            },
            "terminal event must be last",
        ),
    ],
)
def test_event_page_rejects_protocol_violations(
    document: dict[str, Any], message: str
) -> None:
    runner = _runner_module()

    with pytest.raises(runner.ContractViolation, match=message):
        runner.validate_event_page(document, after=0, limit=100)


def test_action_digest_model_is_strict_and_forbids_extra_fields() -> None:
    _runner_module()
    from orbbec_task_contract.models import ActionDigestInput

    with pytest.raises(ValidationError):
        ActionDigestInput.model_validate(
            {
                "platform_task_id": "0d8f0764-91be-4af5-b4d8-e79d58ab3b07",
                "action_seq": 0,
                "action_kind": "voc.submit",
                "parameters": {},
                "summary": "excluded",
            }
        )


STRICT_MODEL_EXAMPLES: dict[str, dict[str, Any]] = {
    "CapabilitiesResponse": {
        "contract_version": "orbbec-http-task/v1",
        "agent_id": "ai-fae-agent",
        "capability_version": 2,
        "supports_actions": True,
        "max_duration_seconds": 600,
        "supported_scopes": ["fae.answer"],
        "supported_event_kinds": ["action_required", "result"],
    },
    "HealthResponse": {
        "contract_version": "orbbec-http-task/v1",
        "status": "healthy",
        "capability_version": 2,
    },
    "TaskResponse": {
        "contract_version": "orbbec-http-task/v1",
        "downstream_task_id": "downstream-1",
        "platform_task_id": "0d8f0764-91be-4af5-b4d8-e79d58ab3b07",
        "status": "running",
        "cancel_requested": False,
        "next_event_seq": 1,
        "terminal": False,
        "created_at": "2026-08-27T10:00:00Z",
        "updated_at": "2026-08-27T10:00:01Z",
    },
    "CreateTaskRequest": {
        "contract_version": "orbbec-http-task/v1",
        "platform_task_id": "0d8f0764-91be-4af5-b4d8-e79d58ab3b07",
        "conversation_ref": "conversation-1",
        "turn_ref": "turn-1",
        "objective": "diagnose USB errors",
        "context_excerpt": ["camera disconnects"],
        "constraints": ["cite evidence"],
        "attachment_refs": [],
        "expected_output": "diagnosis",
        "capability_version": 2,
        "idempotency_key": "create-1",
        "deadline_at": "2026-08-27T10:15:00Z",
        "authorized_scopes": ["fae.answer"],
    },
    "CreateTaskReceipt": {
        "contract_version": "orbbec-http-task/v1",
        "downstream_task_id": "downstream-1",
        "status": "queued",
        "next_event_seq": 1,
        "duplicate": False,
    },
    "MessageRequest": {
        "contract_version": "orbbec-http-task/v1",
        "message_seq": 1,
        "content": "new evidence",
        "attachment_refs": [],
        "idempotency_key": "message-1",
    },
    "MessageReceipt": {
        "contract_version": "orbbec-http-task/v1",
        "downstream_task_id": "downstream-1",
        "message_seq": 1,
        "status": "accepted",
        "duplicate": False,
    },
    "CancelRequest": {
        "contract_version": "orbbec-http-task/v1",
        "idempotency_key": "cancel-1",
    },
    "CancelReceipt": {
        "contract_version": "orbbec-http-task/v1",
        "downstream_task_id": "downstream-1",
        "cancel_request_id": "cancel-request-1",
        "status": "cancel_requested",
        "duplicate": False,
    },
    "ActionExecuteRequest": {
        "contract_version": "orbbec-http-task/v1",
        "action_id": "7d8f0764-91be-4af5-b4d8-e79d58ab3b07",
        "action_digest": "0" * 64,
        "idempotency_key": "execute-1",
    },
    "ActionExecuteReceipt": {
        "contract_version": "orbbec-http-task/v1",
        "action_id": "7d8f0764-91be-4af5-b4d8-e79d58ab3b07",
        "execution_id": "execution-1",
        "status": "queued",
        "duplicate": False,
    },
    "ErrorEnvelope": {
        "contract_version": "orbbec-http-task/v1",
        "error": {
            "code": "scope_denied",
            "message": "task scope is not authorized",
            "details": {},
        },
    },
}


@pytest.mark.parametrize(("model_name", "document"), STRICT_MODEL_EXAMPLES.items())
def test_frozen_models_accept_exact_documents_and_reject_unknown_fields(
    model_name: str, document: dict[str, Any]
) -> None:
    _runner_module()
    from orbbec_task_contract import models

    model = getattr(models, model_name, None)
    assert model is not None, f"missing frozen model {model_name}"
    encoded = json.dumps(document, ensure_ascii=False).encode("utf-8")
    assert model.model_validate_json(encoded)

    with pytest.raises(ValidationError):
        model.model_validate_json(
            json.dumps({**document, "unknown": "rejected"}).encode("utf-8")
        )


def test_integer_fields_reject_json_booleans() -> None:
    _runner_module()
    from orbbec_task_contract import models

    document = {**STRICT_MODEL_EXAMPLES["CapabilitiesResponse"]}
    document["capability_version"] = True
    with pytest.raises(ValidationError):
        models.CapabilitiesResponse.model_validate_json(json.dumps(document))


def test_event_page_requires_contract_and_downstream_identity() -> None:
    _runner_module()
    from orbbec_task_contract.models import EventPage

    valid = {
        "contract_version": "orbbec-http-task/v1",
        "downstream_task_id": "downstream-1",
        "events": [],
        "next_after": 0,
        "terminal": False,
    }
    assert EventPage.model_validate_json(json.dumps(valid))
    for missing in ("contract_version", "downstream_task_id"):
        invalid = {key: value for key, value in valid.items() if key != missing}
        with pytest.raises(ValidationError):
            EventPage.model_validate_json(json.dumps(invalid))


def _write_token_broker(
    path: Path, *, response: str = '{"token":"issued-token"}', exit_code: int = 0
) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "assert request['platform_task_id']\n"
        f"print({response!r})\n"
        f"raise SystemExit({exit_code})\n",
        "utf-8",
    )
    path.chmod(0o700)
    return path


def _token_request():
    _runner_module()
    from orbbec_task_contract.models import TokenBrokerRequest

    return TokenBrokerRequest.model_validate_json(
        json.dumps(
            {
                "profile": "valid",
                "agent_id": "ai-fae-agent",
                "platform_task_id": "0d8f0764-91be-4af5-b4d8-e79d58ab3b07",
                "capability_version": 2,
                "authorized_scopes": ["fae.answer"],
                "task_deadline_at": "2026-08-27T10:15:00Z",
                "action_execution_deadline_at": None,
            }
        )
    )


def test_token_broker_issues_a_dynamic_task_token(tmp_path: Path) -> None:
    from orbbec_task_contract.token_broker import TaskTokenBroker

    executable = _write_token_broker(tmp_path / "broker with spaces")
    broker = TaskTokenBroker(executable)

    assert broker.issue(_token_request()) == "issued-token"


def test_token_broker_requires_an_absolute_executable_path() -> None:
    from orbbec_task_contract.token_broker import TaskTokenBroker

    with pytest.raises(ValueError, match="absolute"):
        TaskTokenBroker(Path("relative-broker"))


@pytest.mark.parametrize(
    ("response", "exit_code", "message"),
    [
        ('{"token":"secret-token","extra":true}', 0, "invalid response"),
        ('{"token":""}', 0, "invalid response"),
        ('{"token":"secret-token"}', 9, "exited with status 9"),
    ],
)
def test_token_broker_rejects_invalid_output_without_leaking_the_token(
    tmp_path: Path, response: str, exit_code: int, message: str
) -> None:
    from orbbec_task_contract.token_broker import TaskTokenBroker, TokenBrokerError

    executable = _write_token_broker(
        tmp_path / "broker", response=response, exit_code=exit_code
    )
    broker = TaskTokenBroker(executable)

    with pytest.raises(TokenBrokerError, match=message) as caught:
        broker.issue(_token_request())
    assert "secret-token" not in str(caught.value)


class RecordingTokenBroker:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def issue(self, request: Any) -> str:
        self.requests.append(request)
        return f"task-token:{request.platform_task_id}"


class StaticTokenBroker:
    def issue(self, request: Any) -> str:
        return "task-token"


class DynamicTokenTarget(ContractTarget):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["x-orbbec-task-contract"] == "orbbec-http-task/v1"
        path = request.url.path
        if request.method == "POST" and path.endswith("/tasks"):
            expected_task_id = self._body(request)["platform_task_id"]
        elif "/tasks/" in path:
            expected_task_id = path.split("/tasks/", 1)[1].split("/", 1)[0]
        else:
            expected_task_id = None
        if expected_task_id is not None:
            assert request.headers["authorization"] == (
                f"Bearer task-token:{expected_task_id}"
            )
        return self._dispatch(request)


def test_runner_issues_tokens_for_each_dynamic_task_id() -> None:
    runner = _runner_module()
    broker = RecordingTokenBroker()
    target = DynamicTokenTarget()

    report = runner.ContractRunner(
        base_url="https://agent.example",
        token_broker=broker,
        agent_id="ai-fae-agent",
        authorized_scopes=("fae.answer",),
        transport=httpx.MockTransport(target),
    ).run()

    assert report.executed_cases
    issued_task_ids = {str(request.platform_task_id) for request in broker.requests}
    assert set(target.tasks).issubset(issued_task_ids)


BASE_URL = os.getenv("ORBBEC_TASK_CONTRACT_BASE_URL")
TOKEN_BROKER = os.getenv("ORBBEC_TASK_CONTRACT_TOKEN_BROKER")
AGENT_ID = os.getenv("ORBBEC_TASK_CONTRACT_AGENT_ID")
SCOPES = tuple(
    scope
    for scope in os.getenv("ORBBEC_TASK_CONTRACT_SCOPES", "").split(",")
    if scope
)


@pytest.mark.skipif(
    not BASE_URL or not TOKEN_BROKER or not AGENT_ID or not SCOPES,
    reason="target repository did not supply its HTTP contract fixture",
)
def test_target_repository_http_task_contract() -> None:
    runner = _runner_module()
    from orbbec_task_contract.token_broker import TaskTokenBroker

    report = runner.ContractRunner(
        base_url=BASE_URL,
        token_broker=TaskTokenBroker(Path(TOKEN_BROKER)),
        agent_id=AGENT_ID,
        authorized_scopes=SCOPES,
    ).run()
    assert report.executed_cases
