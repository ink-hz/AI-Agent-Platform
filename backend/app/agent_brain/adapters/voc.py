from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid5

from app.agent_brain.action_models import (
    ActionProposal,
    proposal_digest,
    stable_action_id,
)
from app.agent_brain.action_service import ActionCommandService
from app.agent_brain.loop_models import NormalizedTaskResult
from app.voc_extension.client import VocProtocolError, VocUpstreamUnavailable

from .base import (
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


class _VocTaskClient(Protocol):
    def create_draft(
        self, *, actor_id: UUID, request_id: UUID, source_text: str
    ) -> dict[str, object]: ...

    def submit_draft(
        self,
        *,
        actor_id: UUID,
        draft_id: UUID,
        request_id: UUID,
        expected_version: int,
    ) -> dict[str, object]: ...


class VocBrainAdapter(AgentAdapter):
    """Durable VOC draft + owner-confirmed submit Adapter.

    The VOC service owns draft and submit idempotency. Platform owns the Action
    decision, encrypted proposal, execution lease, and public event ordering.
    Every identifier is derived from the durable Brain task, so process restarts
    replay the same create/submit requests rather than duplicate business writes.
    """

    supports_cancellation = False
    capabilities = AdapterCapabilities(
        supports_persistent_session=True,
        supports_followup_message=False,
        supports_progress_events=True,
        supports_thinking_summary=False,
        supports_cancel=False,
        supports_attachments=False,
        typical_latency_seconds=30,
    )

    def __init__(
        self,
        client: _VocTaskClient,
        actions: ActionCommandService,
        *,
        worker_id: str = "voc-action",
        action_ttl_seconds: int = 7200,
        execution_lease_seconds: int = 45,
        now=lambda: datetime.now(timezone.utc),
    ) -> None:
        if (
            not hasattr(client, "create_draft")
            or not hasattr(client, "submit_draft")
            or not isinstance(actions, ActionCommandService)
            or type(worker_id) is not str
            or not worker_id
            or type(action_ttl_seconds) is not int
            or not 60 <= action_ttl_seconds <= 86400
            or type(execution_lease_seconds) is not int
            or not 1 <= execution_lease_seconds <= 300
            or not callable(now)
        ):
            raise ValueError("VOC Brain Adapter configuration invalid")
        self._client = client
        self._actions = actions
        self._worker_id = worker_id
        self._action_ttl_seconds = action_ttl_seconds
        self._execution_lease_seconds = execution_lease_seconds
        self._now = now

    @staticmethod
    def _session_id(task_id: UUID) -> str:
        return f"voc-task:{task_id}"

    @staticmethod
    def _task_id(child_session_id: str) -> UUID:
        try:
            prefix, value = child_session_id.split(":", 1)
            if prefix != "voc-task":
                raise ValueError
            return UUID(value)
        except (AttributeError, TypeError, ValueError):
            raise LookupError("VOC child session not found") from None

    @staticmethod
    def _source_text(task: AdapterTask) -> str:
        objective = task.context.get("objective")
        excerpts = task.context.get("context_excerpt", ())
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("VOC task objective required")
        lines = [objective.strip()]
        if isinstance(excerpts, (list, tuple)):
            lines.extend(
                item.strip()
                for item in excerpts
                if isinstance(item, str) and item.strip()
            )
        return "\n".join(lines)[:4000]

    def start_session(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> ChildSessionReceipt:
        if (
            not isinstance(task, AdapterTask)
            or not isinstance(delivery, AdapterDelivery)
            or task.agent_id != "voc"
            or task.requester_subject is None
        ):
            raise ValueError("VOC task dispatch invalid")
        create_request_id = uuid5(task.task_id, "voc-draft-create")
        try:
            draft = self._client.create_draft(
                actor_id=task.requester_subject.internal_user_id,
                request_id=create_request_id,
                source_text=self._source_text(task),
            )
        except (VocProtocolError, VocUpstreamUnavailable):
            return ChildSessionReceipt(
                False,
                self._session_id(task.task_id),
                None,
            )
        try:
            draft_id = UUID(str(draft["draft_id"]))
            version = draft["version"]
            if type(version) is not int or version <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("VOC draft response invalid") from None
        action_id = stable_action_id(task.task_id, 1)
        parameters = {
            "draft_id": str(draft_id),
            "expected_version": version,
        }
        digest = proposal_digest(
            platform_task_id=task.task_id,
            action_seq=1,
            action_kind="voc.submit",
            parameters=parameters,
        )
        self._actions.propose(
            ActionProposal(
                action_id=action_id,
                platform_task_id=task.task_id,
                action_seq=1,
                action_kind="voc.submit",
                summary="提交本次 VOC 草稿",
                impact="确认后将把草稿写入正式 VOC 业务记录。",
                parameters=parameters,
                action_digest=digest,
                expires_at=self._now()
                + timedelta(seconds=self._action_ttl_seconds),
                execution_timeout_seconds=300,
            )
        )
        return ChildSessionReceipt(
            True,
            self._session_id(task.task_id),
            action_id,
        )

    def read_events(
        self, child_session_id: str, *, after: int
    ) -> tuple[AdapterEvent, ...]:
        if type(after) is not int or after < 0:
            raise ValueError("VOC event cursor invalid")
        task_id = self._task_id(child_session_id)
        state = self._actions.for_task(task_id)
        if state is None:
            raise LookupError("VOC Action not found")
        events = [
            AdapterEvent(
                1,
                "work_update",
                "agent",
                f"voc:{task_id}:draft",
                state.created_at,
                {"summary": "VOC 草稿已整理完成，等待用户确认。"},
            ),
            AdapterEvent(
                2,
                "action_required",
                "agent",
                f"voc:{state.projection.action_id}:action",
                state.created_at,
                {
                    "action_id": str(state.projection.action_id),
                    "action_kind": state.projection.action_kind,
                    "action_digest": state.projection.action_digest,
                    "summary": state.projection.summary,
                    "impact": state.projection.impact,
                },
            ),
        ]
        if (
            state.projection.status == "confirmed"
            and state.projection.execution_status in {"queued", "running"}
        ):
            lease = self._actions.lease_execution(
                task_id,
                worker_id=self._worker_id,
                lease_seconds=self._execution_lease_seconds,
            )
            if lease is not None:
                if lease.execution_deadline_at <= self._now():
                    self._actions.finish_execution(
                        lease,
                        {"reason_code": "action_execution_timeout"},
                        succeeded=False,
                    )
                else:
                    try:
                        result = self._client.submit_draft(
                            actor_id=lease.actor_id,
                            draft_id=UUID(str(lease.parameters["draft_id"])),
                            request_id=lease.action_id,
                            expected_version=int(
                                lease.parameters["expected_version"]
                            ),
                        )
                    except VocUpstreamUnavailable:
                        return tuple(event for event in events if event.seq > after)
                    except (KeyError, TypeError, ValueError, VocProtocolError):
                        self._actions.finish_execution(
                            lease,
                            {"reason_code": "voc_action_protocol_error"},
                            succeeded=False,
                        )
                    else:
                        self._actions.finish_execution(
                            lease, result, succeeded=True
                        )
            state = self._actions.for_task(task_id)
            if state is None:
                raise LookupError("VOC Action not found")
        if state.projection.status in {"rejected", "superseded"}:
            events.append(
                self._terminal_event(
                    task_id,
                    3,
                    "cancelled",
                    NormalizedTaskResult(
                        status="cancelled",
                        summary="用户未授权提交 VOC 草稿，本任务已停止。",
                        deliverables=(),
                        evidence=(),
                        limitations=("草稿未写入正式 VOC 记录。",),
                        attachment_refs=(),
                    ),
                    created_at=state.updated_at,
                )
            )
        elif state.projection.status == "expired":
            events.append(
                self._terminal_event(
                    task_id,
                    3,
                    "timeout",
                    NormalizedTaskResult(
                        status="timed_out",
                        summary="VOC 提交确认已过期，本任务未执行。",
                        deliverables=(),
                        evidence=(),
                        limitations=("需要重新生成确认请求。",),
                        attachment_refs=(),
                    ),
                    created_at=state.updated_at,
                )
            )
        elif state.projection.execution_status == "completed":
            result = state.execution_result or {}
            voc_no = result.get("voc_no")
            deliverables = (voc_no,) if isinstance(voc_no, str) else ()
            events.append(
                self._terminal_event(
                    task_id,
                    3,
                    "result",
                    NormalizedTaskResult(
                        status="completed",
                        summary="VOC 草稿已按用户确认提交。",
                        deliverables=deliverables,
                        evidence=(),
                        limitations=(),
                        attachment_refs=(),
                    ),
                    created_at=state.updated_at,
                )
            )
        elif state.projection.execution_status == "failed":
            events.append(
                self._terminal_event(
                    task_id,
                    3,
                    "error",
                    NormalizedTaskResult(
                        status="failed",
                        summary="VOC 草稿提交失败。",
                        deliverables=(),
                        evidence=(),
                        limitations=("上游未完成业务写入。",),
                        attachment_refs=(),
                    ),
                    created_at=state.updated_at,
                )
            )
        return tuple(event for event in events if event.seq > after)

    def _terminal_event(
        self,
        task_id: UUID,
        seq: int,
        kind,
        result: NormalizedTaskResult,
        *,
        created_at: datetime,
    ) -> AdapterEvent:
        return AdapterEvent(
            seq,
            kind,
            "agent",
            f"voc:{task_id}:terminal",
            created_at,
            result.model_dump(mode="json"),
        )

    def send_message(
        self,
        child_session_id: str,
        message: AdapterMessage,
        delivery: AdapterDelivery,
    ) -> MessageDeliveryReceipt:
        raise LookupError("VOC follow-up is not supported")

    def request_stop(
        self,
        child_session_id: str,
        reason: str,
        delivery: AdapterDelivery,
    ) -> StopDeliveryReceipt:
        return StopDeliveryReceipt(False, False)

    def dispatch(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> DispatchReceipt:
        receipt = self.start_session(task, delivery)
        return DispatchReceipt(receipt.accepted, None, receipt.external_run_id)

    def request_cancel(self, task: AdapterTask) -> CancelReceipt:
        return CancelReceipt(False)


__all__ = ["VocBrainAdapter"]
