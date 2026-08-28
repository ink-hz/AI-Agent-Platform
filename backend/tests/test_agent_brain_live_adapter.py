from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.agent_brain.adapters.base import (
    AdapterDelivery,
    AdapterEvent,
    AdapterMessage,
    AdapterRegistry,
    AdapterTask,
    AgentEventProtocolError,
)
from app.agent_brain.adapters.reference import ReferenceAdapter
from app.agent_brain.loop_repository import AdapterSessionPoll
from app.agent_brain.loop_runtime import BrainLoopRuntime

NOW = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)
TASK_ID = UUID("00000000-0000-4000-8000-000000000201")


def _task() -> AdapterTask:
    return AdapterTask(
        task_id=TASK_ID,
        loop_id=UUID("00000000-0000-4000-8000-000000000202"),
        agent_id="hr-bot",
        context={"objective": "分析候选人能力组合"},
        effective_deadline_at=NOW + timedelta(minutes=10),
    )


def _delivery(attempt: int = 1, *, key: str | None = None) -> AdapterDelivery:
    return AdapterDelivery(
        delivery_id=uuid4(),
        attempt=attempt,
        idempotency_key=key or f"reference-delivery-{attempt:02d}",
    )


def _message(seq: int = 2, text: str = "请补充英文环境的公开证据") -> AdapterMessage:
    return AdapterMessage(seq=seq, text=text, created_at=NOW)


def test_reference_adapter_supports_live_session() -> None:
    adapter = ReferenceAdapter()
    opened = adapter.start_session(_task(), _delivery())

    assert opened.accepted is True
    assert opened.child_session_id == adapter.start_session(
        _task(), _delivery(key="reference-delivery-01")
    ).child_session_id
    initial = adapter.read_events(opened.child_session_id, after=0)
    assert [event.kind for event in initial] == [
        "thinking_summary",
        "work_update",
        "message",
        "result",
    ]
    assert initial[0].source == "provider"
    assert all(event.source_ref for event in initial)

    sent = adapter.send_message(
        opened.child_session_id, _message(), _delivery(2)
    )
    replay = adapter.send_message(
        opened.child_session_id,
        _message(),
        _delivery(2, key="reference-delivery-02"),
    )
    assert sent == replay
    followup = adapter.read_events(opened.child_session_id, after=4)
    assert [event.kind for event in followup] == ["message", "result"]

    capabilities = adapter.capabilities
    assert capabilities.supports_persistent_session is True
    assert capabilities.supports_followup_message is True
    assert capabilities.supports_progress_events is True
    assert capabilities.supports_thinking_summary is True
    assert capabilities.supports_cancel is True
    assert capabilities.supports_attachments is False
    assert capabilities.typical_latency_seconds == 1


def test_reference_adapter_rejects_conflicting_delivery_replay() -> None:
    adapter = ReferenceAdapter()
    opened = adapter.start_session(_task(), _delivery(key="stable-start-key"))
    adapter.send_message(
        opened.child_session_id,
        _message(text="第一条追问"),
        _delivery(2, key="stable-followup-key"),
    )

    try:
        adapter.send_message(
            opened.child_session_id,
            _message(text="同键不同内容"),
            _delivery(2, key="stable-followup-key"),
        )
    except ValueError as error:
        assert str(error) == "Adapter delivery conflict"
    else:
        raise AssertionError("conflicting delivery replay must fail")


def test_reference_adapter_stop_is_idempotent_and_factual() -> None:
    adapter = ReferenceAdapter()
    opened = adapter.start_session(_task(), _delivery())
    delivery = _delivery(2, key="stable-stop-key")

    first = adapter.request_stop(opened.child_session_id, "用户停止", delivery)
    replay = adapter.request_stop(opened.child_session_id, "用户停止", delivery)

    assert first == replay
    assert first.accepted is True
    assert first.supported is True


def test_event_gap_fails_only_selected_task_without_escaping_worker_phase() -> None:
    task_id = uuid4()
    loop_id = uuid4()
    failures: list[UUID] = []

    class GapAdapter(ReferenceAdapter):
        def read_events(self, child_session_id: str, *, after: int):
            assert child_session_id == "remote-gap-task"
            assert after == 0
            return (
                AdapterEvent(
                    seq=2,
                    kind="work_update",
                    source="agent",
                    source_ref="remote-gap-task",
                    created_at=NOW,
                    payload={"phase": "started"},
                ),
            )

    class Repository:
        def next_adapter_session_poll(self):
            return AdapterSessionPoll(
                task_id=task_id,
                loop_id=loop_id,
                agent_id="fae-bot",
                adapter_kind="gap",
                child_session_id="remote-gap-task",
            )

        def fail_agent_task_protocol(self, selected_task_id):
            failures.append(selected_task_id)
            return True

        def touch_adapter_session(self, _task_id):
            raise AssertionError("protocol-invalid task must not advance its session")

    class Collaboration:
        def append_task_event_and_wake(self, _event):
            raise AssertionError("gapped event page must be rejected before append")

    adapters = AdapterRegistry()
    adapters.register("gap", GapAdapter())
    runtime = object.__new__(BrainLoopRuntime)
    runtime._repository = Repository()
    runtime._collaboration = Collaboration()
    runtime._adapters = adapters

    assert runtime.reconcile_one() is True
    assert failures == [task_id]


def test_adapter_protocol_error_is_converted_to_task_local_failure() -> None:
    task_id = uuid4()
    failures: list[UUID] = []

    class InvalidPageAdapter(ReferenceAdapter):
        def read_events(self, _child_session_id: str, *, after: int):
            assert after == 0
            raise AgentEventProtocolError("private upstream detail")

    class Repository:
        def next_adapter_session_poll(self):
            return AdapterSessionPoll(
                task_id=task_id,
                loop_id=uuid4(),
                agent_id="admin-bot",
                adapter_kind="invalid_page",
                child_session_id="remote-invalid-page",
            )

        def fail_agent_task_protocol(self, selected_task_id):
            failures.append(selected_task_id)
            return True

    class Collaboration:
        pass

    adapters = AdapterRegistry()
    adapters.register("invalid_page", InvalidPageAdapter())
    runtime = object.__new__(BrainLoopRuntime)
    runtime._repository = Repository()
    runtime._collaboration = Collaboration()
    runtime._adapters = adapters

    assert runtime.reconcile_one() is True
    assert failures == [task_id]
