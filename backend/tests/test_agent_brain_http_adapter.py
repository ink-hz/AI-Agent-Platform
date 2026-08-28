from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.agent_brain.adapters.base import (
    AdapterDelivery,
    AdapterMessage,
    AdapterRegistry,
    AdapterTask,
    AgentEventProtocolError,
)
from app.agent_brain.adapters.http_task import HttpTaskAdapter
from app.agent_brain.worker_runtime import register_http_task_adapters
from app.execution_relay.models import RequesterSubject

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
TASK_ID = UUID("00000000-0000-4000-8000-000000000201")
LOOP_ID = UUID("00000000-0000-4000-8000-000000000202")
CONVERSATION_ID = UUID("00000000-0000-4000-8000-000000000203")
TURN_ID = UUID("00000000-0000-4000-8000-000000000204")
USER_ID = UUID("00000000-0000-4000-8000-000000000205")


class RecordingIssuer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def issue(self, **values) -> str:
        self.calls.append(values)
        return "signed-task-token"


def _task() -> AdapterTask:
    return AdapterTask(
        task_id=TASK_ID,
        loop_id=LOOP_ID,
        conversation_id=CONVERSATION_ID,
        turn_id=TURN_ID,
        agent_id="ai-fae-agent",
        capability_version=2,
        context={
            "objective": "诊断相机掉帧",
            "context_excerpt": ["日志显示 USB 重传"],
            "constraints": ["只引用可核实证据"],
            "attachment_refs": ["00000000-0000-4000-8000-000000000206"],
            "expected_output": "给出诊断结论与下一步",
        },
        effective_deadline_at=NOW + timedelta(minutes=10),
        requester_subject=RequesterSubject(
            internal_user_id=USER_ID,
            display_name="苍渊",
        ),
    )


def _delivery(kind="initial", *, seq=None) -> AdapterDelivery:
    return AdapterDelivery(
        delivery_id=UUID("00000000-0000-4000-8000-000000000207"),
        attempt=1,
        idempotency_key="delivery-key-1",
        delivery_kind=kind,
        source_message_seq=seq,
    )


def _adapter(client: httpx.Client, issuer: RecordingIssuer) -> HttpTaskAdapter:
    return HttpTaskAdapter(
        client,
        base_url="http://127.0.0.1:18000",
        token_issuer=issuer,
        agent_id="ai-fae-agent",
        audience="ai-fae-agent",
        authorized_scopes=("fae.answer",),
    )


@respx.mock
def test_http_adapter_creates_strict_task_with_bound_identity() -> None:
    route = respx.post("http://127.0.0.1:18000/internal/platform/v1/tasks").mock(
        return_value=httpx.Response(
            202,
            json={
                "contract_version": "orbbec-http-task/v1",
                "downstream_task_id": "fae-task-41",
                "status": "queued",
                "next_event_seq": 1,
                "duplicate": False,
            },
        )
    )
    issuer = RecordingIssuer()

    receipt = _adapter(httpx.Client(), issuer).start_session(_task(), _delivery())

    assert receipt.accepted is True
    assert receipt.child_session_id == "fae-task-41"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer signed-task-token"
    assert request.headers["x-orbbec-task-contract"] == "orbbec-http-task/v1"
    assert json.loads(request.content) == {
        "contract_version": "orbbec-http-task/v1",
        "platform_task_id": str(TASK_ID),
        "conversation_ref": str(CONVERSATION_ID),
        "turn_ref": str(TURN_ID),
        "objective": "诊断相机掉帧",
        "context_excerpt": ["日志显示 USB 重传"],
        "constraints": ["只引用可核实证据"],
        "attachment_refs": ["00000000-0000-4000-8000-000000000206"],
        "expected_output": "给出诊断结论与下一步",
        "capability_version": 2,
        "idempotency_key": "delivery-key-1",
        "deadline_at": "2026-08-27T10:10:00Z",
        "authorized_scopes": ["fae.answer"],
    }
    assert issuer.calls == [
        {
            "audience": "ai-fae-agent",
            "internal_user_id": USER_ID,
            "agent_id": "ai-fae-agent",
            "agent_task_id": TASK_ID,
            "capability_version": 2,
            "authorized_scopes": ("fae.answer",),
            "task_deadline_at": NOW + timedelta(minutes=10),
            "action_execution_deadline_at": None,
            "request_id": _delivery().delivery_id,
        }
    ]


@respx.mock
def test_http_adapter_always_uses_nonblocking_finite_event_pages() -> None:
    route = respx.get(
        "http://127.0.0.1:18000/internal/platform/v1/tasks/fae-task-41/events"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "contract_version": "orbbec-http-task/v1",
                "downstream_task_id": "fae-task-41",
                "events": [
                    {
                        "seq": 8,
                        "kind": "work_update",
                        "created_at": "2026-08-27T10:00:01Z",
                        "payload": {"phase": "started"},
                    },
                    {
                        "seq": 9,
                        "kind": "finding",
                        "created_at": "2026-08-27T10:00:02Z",
                        "payload": {"summary": "USB 重传率偏高"},
                    },
                ],
                "next_after": 9,
                "terminal": False,
            },
        )
    )
    issuer = RecordingIssuer()

    events = _adapter(httpx.Client(), issuer).read_events(
        "fae-task-41", after=7, task=_task()
    )

    assert [event.seq for event in events] == [8, 9]
    assert [event.kind for event in events] == ["work_update", "work_update"]
    assert events[1].payload["kind"] == "finding"
    assert dict(route.calls.last.request.url.params) == {
        "after": "7",
        "limit": "100",
        "wait_seconds": "0",
    }


@pytest.mark.parametrize(
    "events,next_after,terminal",
    [
        (
            [
                {
                    "seq": 9,
                    "kind": "work_update",
                    "created_at": "2026-08-27T10:00:01Z",
                    "payload": {},
                }
            ],
            9,
            False,
        ),
        (
            [
                {
                    "seq": 8,
                    "kind": "result",
                    "created_at": "2026-08-27T10:00:01Z",
                    "payload": {"summary": "done"},
                },
                {
                    "seq": 9,
                    "kind": "work_update",
                    "created_at": "2026-08-27T10:00:02Z",
                    "payload": {},
                },
            ],
            9,
            True,
        ),
        (
            [
                {
                    "seq": 8,
                    "kind": "progress",
                    "created_at": "2026-08-27T10:00:01Z",
                    "payload": {},
                }
            ],
            8,
            False,
        ),
    ],
)
@respx.mock
def test_http_adapter_rejects_gaps_terminal_reversal_and_legacy_kinds(
    events, next_after, terminal
) -> None:
    respx.get(
        "http://127.0.0.1:18000/internal/platform/v1/tasks/fae-task-41/events"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "contract_version": "orbbec-http-task/v1",
                "downstream_task_id": "fae-task-41",
                "events": events,
                "next_after": next_after,
                "terminal": terminal,
            },
        )
    )

    with pytest.raises(AgentEventProtocolError):
        _adapter(httpx.Client(), RecordingIssuer()).read_events(
            "fae-task-41", after=7, task=_task()
        )


@respx.mock
def test_http_adapter_sends_followup_and_stop_with_task_bound_token() -> None:
    message_route = respx.post(
        "http://127.0.0.1:18000/internal/platform/v1/tasks/fae-task-41/messages"
    ).mock(
        return_value=httpx.Response(
            202,
            json={
                "contract_version": "orbbec-http-task/v1",
                "downstream_task_id": "fae-task-41",
                "message_seq": 2,
                "status": "accepted",
                "duplicate": False,
            },
        )
    )
    stop_route = respx.post(
        "http://127.0.0.1:18000/internal/platform/v1/tasks/fae-task-41/cancel"
    ).mock(
        return_value=httpx.Response(
            202,
            json={
                "contract_version": "orbbec-http-task/v1",
                "downstream_task_id": "fae-task-41",
                "cancel_request_id": "cancel-1",
                "status": "cancel_requested",
                "duplicate": False,
            },
        )
    )
    adapter = _adapter(httpx.Client(), RecordingIssuer())

    message = adapter.send_message(
        "fae-task-41",
        AdapterMessage(2, "补充 USB 抓包", NOW),
        _delivery("followup", seq=2),
        task=_task(),
    )
    stopped = adapter.request_stop(
        "fae-task-41",
        "用户停止",
        _delivery("stop"),
        task=_task(),
    )

    assert message.accepted is True
    assert stopped.accepted is True
    assert stopped.supported is True
    assert json.loads(message_route.calls.last.request.content)["message_seq"] == 2
    assert json.loads(stop_route.calls.last.request.content) == {
        "contract_version": "orbbec-http-task/v1",
        "idempotency_key": "delivery-key-1",
    }


def test_worker_registers_http_adapters_only_from_complete_configuration(
    tmp_path,
) -> None:
    private_key = tmp_path / "task-token.key"
    private_key.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
    private_key.chmod(0o600)
    configured = {
        "PLATFORM_TASK_SIGNING_PRIVATE_KEY_FILE": str(private_key),
        "PLATFORM_TASK_SIGNING_KEY_ID": "platform-task-v1",
        "PLATFORM_FAE_TASK_BASE_URL": "http://127.0.0.1:18000",
        "PLATFORM_ADMIN_TASK_BASE_URL": "http://127.0.0.1:18001",
    }
    registry = AdapterRegistry()

    selected = register_http_task_adapters(registry, httpx.Client(), environ=configured)

    assert selected == ("fae_http", "admin_http")
    assert registry.is_registered("fae_http") is True
    assert registry.is_registered("admin_http") is True

    empty = AdapterRegistry()
    assert register_http_task_adapters(empty, httpx.Client(), environ={}) == ()
    assert empty.registered_kinds == ()


def test_worker_rejects_partial_http_adapter_configuration() -> None:
    with pytest.raises(RuntimeError, match="HTTP Task Adapter configuration"):
        register_http_task_adapters(
            AdapterRegistry(),
            httpx.Client(),
            environ={
                "PLATFORM_FAE_TASK_BASE_URL": "http://127.0.0.1:18000",
            },
        )
