from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.agent_brain.adapters.base import (
    AdapterDelivery,
    AdapterMessage,
    AdapterTask,
)
from app.agent_brain.adapters.metabot_local import MetaBotLocalAdapter
from app.execution_relay.models import RelayEvent
from app.execution_relay.repository import ExecutionRelayNotFound, RelayJobState

TASK_ID = UUID("00000000-0000-4000-8000-000000000701")
LOOP_ID = UUID("00000000-0000-4000-8000-000000000702")
NOW = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)


class Relay:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.payloads = []
        self.events_by_run: dict[UUID, tuple[RelayEvent, ...]] = {}

    def has_active_worker(self, agent_id, *, freshness_seconds):
        return self.available and agent_id != "agent-brain-bot"

    def enqueue(self, payload):
        self.payloads.append(payload)
        return payload.run_id

    def job_state(self, run_id):
        if not any(payload.run_id == run_id for payload in self.payloads):
            raise ExecutionRelayNotFound()
        terminal = any(
            event.event_type in {"agent.result", "agent.error"}
            for event in self.events_by_run.get(run_id, ())
        )
        return RelayJobState(
            run_id=run_id,
            status="completed" if terminal else "running",
            cancel_requested=False,
            created_at=NOW,
            updated_at=NOW,
            lease_expires_at=None,
            terminal_at=NOW if terminal else None,
            database_now=NOW,
            job_kind="metabot_local",
        )

    def events(self, run_id):
        self.job_state(run_id)
        return self.events_by_run.get(run_id, ())

    def request_cancel(self, run_id):
        return True


def task(*, agent_id: str = "hr-bot") -> AdapterTask:
    return AdapterTask(
        task_id=TASK_ID,
        loop_id=LOOP_ID,
        agent_id=agent_id,
        context={"objective": "评估候选人"},
        effective_deadline_at=NOW + timedelta(minutes=15),
    )


def delivery(kind: str, seq: int | None = None) -> AdapterDelivery:
    suffix = seq or 1
    return AdapterDelivery(
        delivery_id=UUID(f"00000000-0000-4000-8000-{suffix:012d}"),
        attempt=1,
        idempotency_key=f"brain:{TASK_ID}:{kind}:{suffix}",
        delivery_kind=kind,  # type: ignore[arg-type]
        source_message_seq=seq,
    )


def test_initial_followup_and_stop_are_independent_v3_relay_commands() -> None:
    relay = Relay()
    adapter = MetaBotLocalAdapter(relay)

    started = adapter.start_session(task(), delivery("initial"))
    followup = adapter.send_message(
        started.child_session_id,
        AdapterMessage(seq=2, text="请补充英文沟通证据", created_at=NOW),
        delivery("followup", 2),
    )
    stopped = adapter.request_stop(
        started.child_session_id, "目标已经变化", delivery("stop")
    )

    assert started.accepted and followup.accepted and stopped.accepted
    assert [payload.message_kind for payload in relay.payloads] == [
        "initial",
        "followup",
        "stop",
    ]
    assert {payload.task_session_id for payload in relay.payloads} == {
        started.child_session_id
    }
    assert relay.payloads[1].parent_run_id == TASK_ID
    assert relay.payloads[2].parent_run_id == TASK_ID
    assert all(payload.agent_id == "hr-bot" for payload in relay.payloads)


def test_real_v3_events_preserve_thinking_and_work_provenance() -> None:
    relay = Relay()
    adapter = MetaBotLocalAdapter(relay)
    session = adapter.start_session(task(), delivery("initial"))
    relay.events_by_run[TASK_ID] = (
        RelayEvent(
            run_id=TASK_ID,
            seq=1,
            event_type="agent.thinking_summary",
            created_at=NOW,
            payload={
                "source": "provider",
                "providerRunRef": "provider-run-1",
                "blockIndex": 0,
                "deltaSeq": 1,
                "text": "正在核对经历组合",
                "status": "streaming",
            },
        ),
        RelayEvent(
            run_id=TASK_ID,
            seq=2,
            event_type="agent.work_update",
            created_at=NOW + timedelta(seconds=1),
            payload={
                "source": "agent_sdk",
                "sourceRef": "task:research-1",
                "eventSeq": 1,
                "kind": "finding",
                "text": "已找到视觉产品经历",
                "status": "running",
            },
        ),
    )

    events = adapter.read_events(session.child_session_id, after=0)

    assert [(event.kind, event.source, event.source_ref) for event in events] == [
        ("thinking_summary", "provider", "provider-run-1"),
        ("work_update", "agent", "task:research-1"),
    ]
    assert events[1].payload["kind"] == "finding"
    assert events[1].payload["relay_run_id"] == str(TASK_ID)
    assert events[1].payload["relay_seq"] == 2


def test_v3_result_is_normalized_without_reclassifying_answer_as_thinking() -> None:
    relay = Relay()
    adapter = MetaBotLocalAdapter(relay)
    session = adapter.start_session(task(), delivery("initial"))
    relay.events_by_run[TASK_ID] = (
        RelayEvent(
            run_id=TASK_ID,
            seq=1,
            event_type="agent.message",
            created_at=NOW,
            payload={
                "source": "provider",
                "providerRunRef": "provider-run-1",
                "text": "这是专业 Agent 的回答正文",
            },
        ),
        RelayEvent(
            run_id=TASK_ID,
            seq=2,
            event_type="agent.result",
            created_at=NOW + timedelta(seconds=1),
            payload={
                "source": "agent_runtime",
                "sourceRef": f"run:{TASK_ID}",
                "result": {
                    "contractVersion": "core_chat_result_v2",
                    "success": True,
                    "outputText": "候选人的视觉项目证据充分。",
                },
            },
        ),
    )

    events = adapter.read_events(session.child_session_id, after=0)

    assert [event.kind for event in events] == ["message", "result"]
    assert events[1].payload["summary"] == "候选人的视觉项目证据充分。"
    assert all(event.kind != "thinking_summary" for event in events)


def test_v4_result_exposes_only_platform_registered_artifact_ids() -> None:
    relay = Relay()
    adapter = MetaBotLocalAdapter(relay)
    adapter.start_session(task(), delivery("initial"))
    attachment_id = UUID("00000000-0000-4000-8000-000000000799")
    relay.events_by_run[TASK_ID] = (
        RelayEvent(
            run_id=TASK_ID,
            seq=1,
            event_type="agent.result",
            created_at=NOW,
            payload={
                "source": "agent_runtime",
                "sourceRef": f"run:{TASK_ID}",
                "result": {
                    "contractVersion": "core_chat_collaboration_v4",
                    "publicAnswerMarkdown": "已完成候选人评估。",
                    "citations": [],
                    "artifacts": [
                        {
                            "attachmentId": str(attachment_id),
                            "artifactKey": "candidate-evaluation",
                            "producerVersionId": "report-v1",
                            "displayName": "候选人评估.pdf",
                            "status": "ready",
                        }
                    ],
                    "completion": "completed",
                    "recovery": None,
                },
            },
        ),
    )

    receipt = adapter.reconcile(task(), next_event_seq=1)

    assert receipt.events[0].result is not None
    assert receipt.events[0].result.summary == "已完成候选人评估。"
    assert receipt.events[0].result.attachment_refs == (attachment_id,)


def test_adapter_never_dispatches_local_brain_or_hides_mac_offline() -> None:
    relay = Relay(available=False)
    adapter = MetaBotLocalAdapter(relay)

    offline = adapter.start_session(task(), delivery("initial"))
    brain = adapter.start_session(task(agent_id="agent-brain-bot"), delivery("initial"))

    assert offline.accepted is False
    assert brain.accepted is False
    assert relay.payloads == []
