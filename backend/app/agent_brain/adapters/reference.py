from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from threading import RLock
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


@dataclass(slots=True)
class _ReferenceSession:
    task: AdapterTask
    child_session_id: str
    events: list[AdapterEvent]
    last_message_seq: int = 1
    deliveries: dict[str, tuple[str, object]] = field(default_factory=dict)
    stopped: bool = False


class ReferenceAdapter(AgentAdapter):
    """Deterministic, network-free Adapter used to prove runtime durability."""

    supports_cancellation = True
    capabilities = AdapterCapabilities(
        supports_persistent_session=True,
        supports_followup_message=True,
        supports_progress_events=True,
        supports_thinking_summary=True,
        supports_cancel=True,
        supports_attachments=False,
        typical_latency_seconds=1,
    )

    def __init__(self) -> None:
        self._sessions: dict[str, _ReferenceSession] = {}
        self._lock = RLock()

    def start_session(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> ChildSessionReceipt:
        if not isinstance(task, AdapterTask) or not isinstance(
            delivery, AdapterDelivery
        ):
            raise ValueError("Adapter start invalid")
        child_session_id = str(uuid5(task.task_id, "reference-child-session"))
        run_id = uuid5(task.task_id, "reference-run:initial")
        receipt = ChildSessionReceipt(True, child_session_id, run_id)
        signature = _digest(
            {
                "task_id": str(task.task_id),
                "loop_id": str(task.loop_id),
                "agent_id": task.agent_id,
                "context": task.context,
            }
        )
        with self._lock:
            existing = self._sessions.get(child_session_id)
            if existing is not None:
                return _deduplicated(existing, delivery.idempotency_key, signature, receipt)
            now = datetime.now(timezone.utc)
            session = _ReferenceSession(
                task=task,
                child_session_id=child_session_id,
                events=[
                    AdapterEvent(
                        1,
                        "thinking_summary",
                        "provider",
                        str(run_id),
                        now,
                        {"summary": "先核对任务边界，再生成确定性结果。"},
                    ),
                    AdapterEvent(
                        2,
                        "work_update",
                        "agent",
                        str(run_id),
                        now,
                        {"kind": "progress", "summary": "正在验证持久协作链路。"},
                    ),
                    AdapterEvent(
                        3,
                        "message",
                        "agent",
                        str(run_id),
                        now,
                        {"text": "Reference Agent 已完成链路检查。"},
                    ),
                    AdapterEvent(
                        4,
                        "result",
                        "agent",
                        str(run_id),
                        now,
                        _reference_result(task).model_dump(mode="json"),
                    ),
                ],
            )
            session.deliveries[delivery.idempotency_key] = (signature, receipt)
            self._sessions[child_session_id] = session
            return receipt

    def send_message(
        self,
        child_session_id: str,
        message: AdapterMessage,
        delivery: AdapterDelivery,
    ) -> MessageDeliveryReceipt:
        if (
            type(child_session_id) is not str
            or not isinstance(message, AdapterMessage)
            or not isinstance(delivery, AdapterDelivery)
        ):
            raise ValueError("Adapter message invalid")
        signature = _digest(
            {"child_session_id": child_session_id, "seq": message.seq, "text": message.text}
        )
        with self._lock:
            session = self._require_session(child_session_id)
            existing = session.deliveries.get(delivery.idempotency_key)
            if existing is not None:
                return _deduplicated(
                    session, delivery.idempotency_key, signature, existing[1]
                )
            if session.stopped or message.seq != session.last_message_seq + 1:
                raise ValueError("Adapter message sequence invalid")
            run_id = uuid5(
                session.task.task_id, f"reference-run:message:{message.seq}"
            )
            receipt = MessageDeliveryReceipt(True, run_id)
            next_seq = len(session.events) + 1
            session.events.extend(
                (
                    AdapterEvent(
                        next_seq,
                        "message",
                        "agent",
                        str(run_id),
                        message.created_at,
                        {"text": f"已收到追问：{message.text}"},
                    ),
                    AdapterEvent(
                        next_seq + 1,
                        "result",
                        "agent",
                        str(run_id),
                        message.created_at,
                        _reference_result(session.task).model_dump(mode="json"),
                    ),
                )
            )
            session.last_message_seq = message.seq
            session.deliveries[delivery.idempotency_key] = (signature, receipt)
            return receipt

    def read_events(
        self, child_session_id: str, *, after: int
    ) -> tuple[AdapterEvent, ...]:
        if type(child_session_id) is not str or type(after) is not int or after < 0:
            raise ValueError("Adapter event cursor invalid")
        with self._lock:
            return tuple(
                event
                for event in self._require_session(child_session_id).events
                if event.seq > after
            )

    def request_stop(
        self,
        child_session_id: str,
        reason: str,
        delivery: AdapterDelivery,
    ) -> StopDeliveryReceipt:
        if (
            type(child_session_id) is not str
            or type(reason) is not str
            or not reason.strip()
            or not isinstance(delivery, AdapterDelivery)
        ):
            raise ValueError("Adapter stop invalid")
        signature = _digest({"child_session_id": child_session_id, "reason": reason})
        receipt = StopDeliveryReceipt(True, True)
        with self._lock:
            session = self._require_session(child_session_id)
            existing = session.deliveries.get(delivery.idempotency_key)
            if existing is not None:
                return _deduplicated(
                    session, delivery.idempotency_key, signature, existing[1]
                )
            session.stopped = True
            session.deliveries[delivery.idempotency_key] = (signature, receipt)
            return receipt

    def dispatch(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> DispatchReceipt:
        opened = self.start_session(task, delivery)
        return DispatchReceipt(
            accepted=opened.accepted,
            result=_reference_result(task),
            external_run_id=opened.external_run_id,
        )

    def request_cancel(self, task: AdapterTask) -> CancelReceipt:
        if not isinstance(task, AdapterTask):
            raise ValueError("Adapter Task invalid")
        return CancelReceipt(accepted=True)

    def _require_session(self, child_session_id: str) -> _ReferenceSession:
        try:
            return self._sessions[child_session_id]
        except KeyError:
            raise LookupError("Adapter child session not found") from None


def _reference_result(task: AdapterTask) -> NormalizedTaskResult:
    return NormalizedTaskResult(
        status="completed",
        summary="Reference Agent 已完成确定性链路验证。",
        deliverables=("durable-loop-reference-result",),
        evidence=(f"task_id={task.task_id}",),
        limitations=("该结果仅验证平台执行链路，不代表业务判断。",),
        attachment_refs=(),
    )


def _deduplicated(session, key: str, signature: str, receipt):
    existing = session.deliveries.get(key)
    if existing is None or existing[0] != signature or existing[1] != receipt:
        raise ValueError("Adapter delivery conflict")
    return receipt


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
