from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from app.agent_brain.adapters.base import (
    AdapterCapabilities,
    AdapterDelivery,
    AdapterEvent,
    AdapterMessage,
    AdapterTask,
    AgentAdapter,
    CancelReceipt,
    ChildSessionReceipt,
    DispatchReceipt,
    MessageDeliveryReceipt,
    StopDeliveryReceipt,
)
from app.agent_brain.loop_models import NormalizedTaskResult
from app.agent_brain.loop_repository import AgentTaskEventInput
from app.execution_relay.models import RelayJobPayload
from app.execution_relay.repository import (
    ExecutionRelayConflict,
    ExecutionRelayNotFound,
    RelayJobState,
)


class _Relay(Protocol):
    def enqueue(self, payload: RelayJobPayload) -> UUID: ...

    def job_state(self, run_id: UUID) -> RelayJobState: ...

    def request_cancel(self, run_id: UUID) -> bool: ...

    def events(self, run_id: UUID): ...

    def has_active_worker(
        self, agent_id: str, *, freshness_seconds: int
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReconciliationReceipt:
    events: tuple[AgentTaskEventInput, ...]
    terminal: bool


class MetaBotLocalAdapter(AgentAdapter):
    """Reliable V2 bridge to local MetaBot professional Agents.

    Enqueue is idempotent on the Brain task id.  A successful dispatch is not
    a completed task; the cloud reconciler consumes relay events separately.
    """

    supports_cancellation = True
    capabilities = AdapterCapabilities(
        supports_persistent_session=False,
        supports_followup_message=False,
        supports_progress_events=True,
        supports_thinking_summary=False,
        supports_cancel=True,
        supports_attachments=False,
        typical_latency_seconds=90,
    )

    def __init__(self, relay: _Relay, *, worker_freshness_seconds: int = 60) -> None:
        if (
            not hasattr(relay, "enqueue")
            or not hasattr(relay, "job_state")
            or not hasattr(relay, "request_cancel")
            or not hasattr(relay, "has_active_worker")
            or type(worker_freshness_seconds) is not int
            or worker_freshness_seconds <= 0
        ):
            raise ValueError("MetaBot local Adapter configuration invalid")
        self._relay = relay
        self._worker_freshness_seconds = worker_freshness_seconds
        self._tasks_by_session: dict[str, AdapterTask] = {}

    def start_session(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> ChildSessionReceipt:
        receipt = self.dispatch(task, delivery)
        child_session_id = str(task.task_id)
        if receipt.accepted:
            self._tasks_by_session[child_session_id] = task
        return ChildSessionReceipt(
            accepted=receipt.accepted,
            child_session_id=child_session_id,
            external_run_id=receipt.external_run_id,
        )

    def send_message(
        self,
        child_session_id: str,
        message: AdapterMessage,
        delivery: AdapterDelivery,
    ) -> MessageDeliveryReceipt:
        del child_session_id, message, delivery
        return MessageDeliveryReceipt(accepted=False, external_run_id=None)

    def read_events(
        self, child_session_id: str, *, after: int
    ) -> tuple[AdapterEvent, ...]:
        task = self._tasks_by_session.get(child_session_id)
        if task is None:
            raise LookupError("Adapter child session not found")
        receipt = self.reconcile(task, next_event_seq=after + 1)
        return tuple(
            AdapterEvent(
                seq=event.seq,
                kind=("result" if event.terminal_status is not None else "work_update"),
                source="adapter",
                source_ref=str(task.task_id),
                created_at=event.created_at,
                payload=event.payload,
            )
            for event in receipt.events
        )

    def request_stop(
        self,
        child_session_id: str,
        reason: str,
        delivery: AdapterDelivery,
    ) -> StopDeliveryReceipt:
        del reason, delivery
        task = self._tasks_by_session.get(child_session_id)
        if task is None:
            return StopDeliveryReceipt(accepted=False, supported=True)
        return StopDeliveryReceipt(
            accepted=self.request_cancel(task).accepted,
            supported=True,
        )

    def dispatch(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> DispatchReceipt:
        if not self._relay.has_active_worker(
            task.agent_id, freshness_seconds=self._worker_freshness_seconds
        ):
            return DispatchReceipt(
                accepted=False,
                result=NormalizedTaskResult(
                    status="unavailable",
                    summary="本地专业 Agent 当前离线，任务未派发。",
                    deliverables=(),
                    evidence=(),
                    limitations=("可稍后重试，其他云端能力不受影响。",),
                    attachment_refs=(),
                ),
            )
        payload = RelayJobPayload(
            run_id=task.task_id,
            conversation_id=task.loop_id,
            trigger_message_id=task.task_id,
            agent_id=task.agent_id,
            prompt=json.dumps(
                task.context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            max_turns=24,
            job_kind="metabot_local",
            requester_subject=task.requester_subject,
        )
        try:
            self._relay.enqueue(payload)
        except ExecutionRelayConflict:
            state = self._relay.job_state(task.task_id)
            if state.job_kind != "metabot_local":
                raise
        return DispatchReceipt(
            accepted=True,
            result=None,
            external_run_id=task.task_id,
        )

    def request_cancel(self, task: AdapterTask) -> CancelReceipt:
        try:
            return CancelReceipt(accepted=self._relay.request_cancel(task.task_id))
        except ExecutionRelayNotFound:
            return CancelReceipt(accepted=False)

    def reconcile(
        self, task: AdapterTask, *, next_event_seq: int
    ) -> ReconciliationReceipt:
        state = self._relay.job_state(task.task_id)
        relay_events = self._relay.events(task.task_id)
        unseen = relay_events[next_event_seq - 1 :]
        normalized: list[AgentTaskEventInput] = []
        terminal_status = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "interrupted": "unavailable",
        }.get(state.status)
        for offset, source in enumerate(unseen):
            seq = next_event_seq + offset
            is_last_terminal = (
                terminal_status is not None and source is relay_events[-1]
            )
            summary = _event_summary(source.payload, terminal_status)
            result = (
                _normalized_result(terminal_status, summary, source.payload)
                if is_last_terminal
                else None
            )
            normalized.append(
                AgentTaskEventInput(
                    task_id=task.task_id,
                    seq=seq,
                    event_type=(
                        f"agent.{terminal_status}"
                        if is_last_terminal
                        else "agent.progress"
                    ),
                    created_at=source.created_at,
                    payload={
                        "status": terminal_status if is_last_terminal else "running",
                        "summary": summary,
                    },
                    terminal_status=terminal_status if is_last_terminal else None,
                    result=result,
                )
            )
        if terminal_status is not None and not normalized:
            summary = _event_summary({}, terminal_status)
            normalized.append(
                AgentTaskEventInput(
                    task_id=task.task_id,
                    seq=next_event_seq,
                    event_type=f"agent.{terminal_status}",
                    created_at=state.terminal_at or datetime.now(timezone.utc),
                    payload={"status": terminal_status, "summary": summary},
                    terminal_status=terminal_status,
                    result=_normalized_result(terminal_status, summary, {}),
                )
            )
        return ReconciliationReceipt(
            events=tuple(normalized), terminal=terminal_status is not None
        )


def _event_summary(payload: dict[str, object], status: str | None) -> str:
    result = payload.get("result")
    if (
        isinstance(result, dict)
        and result.get("contractVersion") == "core_chat_result_v2"
        and result.get("success") is True
    ):
        output_text = result.get("outputText")
        if isinstance(output_text, str) and output_text.strip():
            return output_text[:32768]
    for key in ("summary", "text", "result", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value[:32768]
    return {
        "completed": "本地专业 Agent 已完成任务。",
        "failed": "本地专业 Agent 执行失败。",
        "cancelled": "本地专业 Agent 已取消任务。",
        "unavailable": "本地专业 Agent 执行被中断。",
    }.get(status, "本地专业 Agent 正在执行。")


def _string_items(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item[:8192] for item in value[:64] if isinstance(item, str) and item)


def _attachment_refs(payload: dict[str, object]) -> tuple[UUID, ...]:
    value = payload.get("attachment_refs")
    if not isinstance(value, list):
        return ()
    result: list[UUID] = []
    for item in value[:32]:
        try:
            selected = UUID(item) if isinstance(item, str) else None
        except ValueError:
            selected = None
        if selected is not None and selected not in result:
            result.append(selected)
    return tuple(result)


def _normalized_result(
    status: str, summary: str, payload: dict[str, object]
) -> NormalizedTaskResult:
    return NormalizedTaskResult(
        status=status,  # type: ignore[arg-type]
        summary=summary,
        deliverables=_string_items(payload, "deliverables"),
        evidence=_string_items(payload, "evidence"),
        limitations=_string_items(payload, "limitations"),
        attachment_refs=_attachment_refs(payload),
    )
