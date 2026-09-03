from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid5

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

    def has_active_worker(self, agent_id: str, *, freshness_seconds: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReconciliationReceipt:
    events: tuple[AgentTaskEventInput, ...]
    terminal: bool


class MetaBotLocalAdapter(AgentAdapter):
    """Reliable Core Chat v3/v4 bridge to local MetaBot professional Agents.

    Enqueue is idempotent on the Brain task id.  A successful dispatch is not
    a completed task; the cloud reconciler consumes relay events separately.
    """

    supports_cancellation = True
    capabilities = AdapterCapabilities(
        supports_persistent_session=True,
        supports_followup_message=True,
        supports_progress_events=True,
        supports_thinking_summary=True,
        supports_cancel=True,
        supports_attachments=True,
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
        if task.agent_id == "agent-brain-bot":
            return ChildSessionReceipt(False, self._session_id(task), None)
        receipt = self.dispatch(task, delivery)
        child_session_id = self._session_id(task)
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
        *,
        task: AdapterTask | None = None,
    ) -> MessageDeliveryReceipt:
        try:
            task_id, loop_id, agent_id = self._session_parts(child_session_id)
        except (TypeError, ValueError):
            return MessageDeliveryReceipt(False, None)
        if not self._worker_available(agent_id) or message.seq <= 1:
            return MessageDeliveryReceipt(False, None)
        parent_run_id = (
            task_id
            if message.seq == 2
            else uuid5(task_id, f"delivery:followup:{message.seq - 1}")
        )
        payload = RelayJobPayload(
            run_id=delivery.delivery_id,
            conversation_id=loop_id,
            trigger_message_id=delivery.delivery_id,
            agent_id=agent_id,
            prompt=message.text,
            max_turns=24,
            job_kind="metabot_local",
            collaboration_contract=self._contract_for_task(
                task or self._tasks_by_session.get(child_session_id)
            ),
            task_session_id=child_session_id,
            message_kind="followup",
            message_seq=message.seq,
            parent_run_id=parent_run_id,
        )
        return MessageDeliveryReceipt(
            accepted=self._enqueue(payload), external_run_id=payload.run_id
        )

    def read_events(
        self,
        child_session_id: str,
        *,
        after: int,
        task: AdapterTask | None = None,
    ) -> tuple[AdapterEvent, ...]:
        del task
        try:
            task_id, _loop_id, _agent_id = self._session_parts(child_session_id)
        except (TypeError, ValueError):
            raise LookupError("Adapter child session not found")
        if type(after) is not int or after < 0:
            raise ValueError("Adapter event cursor invalid")
        source_events = self._collaboration_events(task_id)
        return tuple(
            self._adapter_event(source, seq=index)
            for index, source in enumerate(source_events, start=1)
            if index > after
        )

    def request_stop(
        self,
        child_session_id: str,
        reason: str,
        delivery: AdapterDelivery,
        *,
        task: AdapterTask | None = None,
    ) -> StopDeliveryReceipt:
        try:
            task_id, loop_id, agent_id = self._session_parts(child_session_id)
        except (TypeError, ValueError):
            return StopDeliveryReceipt(False, True)
        if not self._worker_available(agent_id):
            return StopDeliveryReceipt(False, True)
        payload = RelayJobPayload(
            run_id=delivery.delivery_id,
            conversation_id=loop_id,
            trigger_message_id=delivery.delivery_id,
            agent_id=agent_id,
            prompt=reason,
            max_turns=24,
            job_kind="metabot_local",
            collaboration_contract=self._contract_for_task(
                task or self._tasks_by_session.get(child_session_id)
            ),
            task_session_id=child_session_id,
            message_kind="stop",
            message_seq=1,
            parent_run_id=task_id,
        )
        return StopDeliveryReceipt(self._enqueue(payload), True)

    def dispatch(self, task: AdapterTask, delivery: AdapterDelivery) -> DispatchReceipt:
        if task.agent_id == "agent-brain-bot" or not self._worker_available(
            task.agent_id
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
            collaboration_contract=self._contract_for_task(task),
            task_session_id=self._session_id(task),
            message_kind="initial",
            message_seq=1,
            input_attachment_grants=task.input_attachment_grants,
            output_write_grant=task.output_write_grant,
        )
        self._enqueue(payload)
        return DispatchReceipt(
            accepted=True,
            result=None,
            external_run_id=task.task_id,
        )

    def _worker_available(self, agent_id: str) -> bool:
        return agent_id != "agent-brain-bot" and self._relay.has_active_worker(
            agent_id, freshness_seconds=self._worker_freshness_seconds
        )

    @staticmethod
    def _contract_for_task(task: AdapterTask | None) -> str:
        if task is not None and (
            task.input_attachment_grants or task.output_write_grant is not None
        ):
            return "core_chat_collaboration_v4"
        return "core_chat_collaboration_v3"

    def _enqueue(self, payload: RelayJobPayload) -> bool:
        try:
            self._relay.enqueue(payload)
        except ExecutionRelayConflict:
            state = self._relay.job_state(payload.run_id)
            if state.job_kind != "metabot_local":
                raise
        return True

    @staticmethod
    def _session_id(task: AdapterTask) -> str:
        return f"metabot:{task.task_id}:{task.loop_id}:{task.agent_id}"

    @staticmethod
    def _session_parts(child_session_id: str) -> tuple[UUID, UUID, str]:
        if type(child_session_id) is not str:
            raise ValueError
        prefix, task_id, loop_id, agent_id = child_session_id.split(":", 3)
        if prefix != "metabot" or not agent_id or agent_id == "agent-brain-bot":
            raise ValueError
        return UUID(task_id), UUID(loop_id), agent_id

    def _collaboration_events(self, task_id: UUID) -> tuple[object, ...]:
        run_ids = [task_id]
        for message_seq in range(2, 6):
            candidate = uuid5(task_id, f"delivery:followup:{message_seq}")
            try:
                self._relay.job_state(candidate)
            except ExecutionRelayNotFound:
                break
            run_ids.append(candidate)
        events = [event for run_id in run_ids for event in self._relay.events(run_id)]
        return tuple(
            sorted(
                events,
                key=lambda event: (event.created_at, str(event.run_id), event.seq),
            )
        )

    @staticmethod
    def _adapter_event(source: object, *, seq: int) -> AdapterEvent:
        event_type = source.event_type
        payload = dict(source.payload)
        payload["relay_run_id"] = str(source.run_id)
        payload["relay_seq"] = source.seq
        kind = {
            "agent.thinking_summary": "thinking_summary",
            "agent.work_update": "work_update",
            "agent.message": "message",
            "agent.artifact": "artifact",
            "agent.question": "question",
            "agent.result": "result",
            "agent.complete": "result",
            "agent.error": "error",
        }.get(event_type, "work_update")
        raw_source = payload.get("source")
        normalized_source = (
            "provider"
            if raw_source == "provider"
            else "adapter"
            if raw_source == "adapter"
            else "agent"
        )
        source_ref = payload.get("providerRunRef") or payload.get("sourceRef")
        if not isinstance(source_ref, str) or not source_ref:
            source_ref = str(source.run_id)
        if kind == "result":
            payload.setdefault("summary", _event_summary(payload, "completed"))
        elif kind == "error":
            payload.setdefault("summary", _event_summary(payload, "failed"))
        return AdapterEvent(
            seq=seq,
            kind=kind,  # type: ignore[arg-type]
            source=normalized_source,  # type: ignore[arg-type]
            source_ref=source_ref,
            created_at=source.created_at,
            payload=payload,
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
        and result.get("contractVersion") == "core_chat_collaboration_v4"
    ):
        public_answer = result.get("publicAnswerMarkdown")
        if isinstance(public_answer, str) and public_answer.strip():
            return public_answer[:32768]
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
    nested = payload.get("result")
    if (
        isinstance(nested, dict)
        and nested.get("contractVersion") == "core_chat_collaboration_v4"
        and isinstance(nested.get("artifacts"), list)
    ):
        value = [
            item.get("attachmentId")
            for item in nested["artifacts"][:32]
            if isinstance(item, dict) and item.get("status") == "ready"
        ]
    else:
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
