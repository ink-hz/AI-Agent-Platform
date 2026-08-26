from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.agent_brain.adapters.base import AdapterDelivery, AdapterTask
from app.agent_brain.adapters.metabot_local import MetaBotLocalAdapter
from app.execution_relay.models import RelayEvent, RequesterSubject
from app.execution_relay.repository import ExecutionRelayConflict, RelayJobState


TASK_ID = UUID("00000000-0000-4000-8000-000000000701")
LOOP_ID = UUID("00000000-0000-4000-8000-000000000702")
NOW = datetime(2026, 8, 24, 4, 0, tzinfo=timezone.utc)


class FakeRelay:
    def __init__(self, *, available: bool = True, status: str = "queued") -> None:
        self.available = available
        self.status = status
        self.payloads = []
        self.cancelled = []
        self.relay_events = ()

    def has_active_worker(self, agent_id, *, freshness_seconds):
        return self.available

    def enqueue(self, payload):
        if self.payloads:
            raise ExecutionRelayConflict()
        self.payloads.append(payload)
        return UUID("00000000-0000-4000-8000-000000000703")

    def job_state(self, run_id):
        if not self.payloads:
            raise RuntimeError
        return RelayJobState(
            run_id=run_id,
            status=self.status,
            cancel_requested=False,
            created_at=NOW,
            updated_at=NOW,
            lease_expires_at=None,
            terminal_at=NOW if self.status in {"completed", "failed", "cancelled", "interrupted"} else None,
            database_now=NOW,
            job_kind="metabot_local",
        )

    def request_cancel(self, run_id):
        self.cancelled.append(run_id)
        return True

    def events(self, run_id):
        return self.relay_events


def _task() -> AdapterTask:
    return AdapterTask(
        task_id=TASK_ID,
        loop_id=LOOP_ID,
        agent_id="hr-bot",
        context={
            "objective": "评估候选人",
            "context_excerpt": "候选人简历摘要",
            "constraints": ["不泄露个人敏感信息"],
            "attachment_refs": [],
            "expected_output": "结构化人才判断",
        },
        effective_deadline_at=NOW + timedelta(minutes=5),
        requester_subject=RequesterSubject(
            internal_user_id=UUID("00000000-0000-4000-8000-000000000705"),
            display_name="苍渊",
        ),
    )


def _delivery() -> AdapterDelivery:
    return AdapterDelivery(
        delivery_id=UUID("00000000-0000-4000-8000-000000000704"),
        attempt=1,
        idempotency_key=f"brain:{TASK_ID}:delivery:1",
    )


def test_metabot_adapter_replay_enqueues_one_relay_job() -> None:
    relay = FakeRelay()
    adapter = MetaBotLocalAdapter(relay, worker_freshness_seconds=60)

    first = adapter.dispatch(_task(), _delivery())
    second = adapter.dispatch(_task(), _delivery())

    assert first.accepted is True
    assert first.external_run_id == TASK_ID
    assert first.result is None
    assert second == first
    assert len(relay.payloads) == 1
    assert relay.payloads[0].run_id == TASK_ID
    assert relay.payloads[0].job_kind == "metabot_local"
    assert relay.payloads[0].result_mode == "internal"
    assert relay.payloads[0].requester_subject is None
    assert "requester_subject" not in relay.payloads[0].prompt


def test_metabot_adapter_returns_fast_unavailable_when_worker_is_offline() -> None:
    adapter = MetaBotLocalAdapter(FakeRelay(available=False), worker_freshness_seconds=60)

    receipt = adapter.dispatch(_task(), _delivery())

    assert receipt.accepted is False
    assert receipt.external_run_id is None
    assert receipt.result is not None
    assert receipt.result.status == "unavailable"
    assert "本地" in receipt.result.summary


def test_metabot_adapter_declares_and_routes_cancellation() -> None:
    relay = FakeRelay()
    adapter = MetaBotLocalAdapter(relay, worker_freshness_seconds=60)

    assert adapter.supports_cancellation is True
    assert adapter.request_cancel(_task()).accepted is True
    assert relay.cancelled == [TASK_ID]


def test_metabot_adapter_normalizes_progress_and_completed_result() -> None:
    relay = FakeRelay(status="completed")
    relay.payloads.append(object())
    relay.relay_events = (
        RelayEvent(
            run_id=TASK_ID,
            seq=1,
            event_type="agent.state",
            created_at=NOW,
            payload={"text": "正在分析", "private": "not forwarded"},
        ),
        RelayEvent(
            run_id=TASK_ID,
            seq=2,
            event_type="agent.complete",
            created_at=NOW + timedelta(seconds=1),
            payload={
                "summary": "候选人匹配",
                "deliverables": ["人才判断"],
                "evidence": ["有视觉项目经验"],
                "limitations": ["需面试确认英文能力"],
            },
        ),
    )
    receipt = MetaBotLocalAdapter(relay).reconcile(_task(), next_event_seq=1)

    assert [event.seq for event in receipt.events] == [1, 2]
    assert [event.event_type for event in receipt.events] == [
        "agent.progress",
        "agent.completed",
    ]
    assert receipt.events[0].payload == {
        "status": "running",
        "summary": "正在分析",
    }
    assert receipt.events[1].result is not None
    assert receipt.events[1].result.deliverables == ("人才判断",)
    assert receipt.terminal is True


def test_metabot_adapter_reads_v2_internal_output_as_task_summary() -> None:
    relay = FakeRelay(status="completed")
    relay.payloads.append(object())
    relay.relay_events = (
        RelayEvent(
            run_id=TASK_ID,
            seq=1,
            event_type="agent.complete",
            created_at=NOW,
            payload={
                "result": {
                    "contractVersion": "core_chat_result_v2",
                    "success": True,
                    "outputText": "候选人的视觉项目证据充分。",
                }
            },
        ),
    )

    receipt = MetaBotLocalAdapter(relay).reconcile(_task(), next_event_seq=1)

    assert receipt.events[0].result is not None
    assert receipt.events[0].result.summary == "候选人的视觉项目证据充分。"
