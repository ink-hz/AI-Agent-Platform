from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json

from app.agent_brain.adapters.base import (
    AdapterDelivery,
    AdapterMessage,
    AdapterRegistry,
    AdapterTask,
)
from app.agent_brain.collaboration_models import AgentTaskPublicEventInput
from app.agent_brain.context_policy import BrainContextPolicy
from app.agent_brain.loop_repository import (
    AgentTaskEventInput,
    BrainLoopRepository,
    ImmediateToolResult,
    ModelStepCommit,
    TaskDispatchSpec,
)
from app.agent_brain.loop_models import NormalizedTaskResult
from app.agent_brain.model_adapter import (
    BrainModelAdapter,
    BrainModelError,
    BrainRequestBuilder,
    ProviderRefused,
)
from app.agent_brain.prompt import BrainSystemPrompt
from app.agent_brain.tool_protocol import (
    DelegateTaskCall,
    ListAgentsCall,
    SubmitAnswerCall,
    ToolLimits,
    ProtocolViolation,
    parse_tool_batch,
)


class BrainLoopRuntime:
    def __init__(
        self,
        *,
        repository: BrainLoopRepository,
        model: BrainModelAdapter | object | None,
        request_builder: BrainRequestBuilder,
        system_prompt: BrainSystemPrompt,
        runtime_registry: object,
        adapters: AdapterRegistry,
        worker_id: str,
        lease_seconds: int,
        context_policy: BrainContextPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._model = model
        self._request_builder = request_builder
        self._system_prompt = system_prompt
        self._runtime_registry = runtime_registry
        self._adapters = adapters
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._context_policy = context_policy or BrainContextPolicy()
        self._collaboration = repository.collaboration_repository()

    def advance_one(self) -> bool:
        if self._model is None or not hasattr(self._model, "complete"):
            return False
        lease = self._repository.lease_step(
            self._worker_id, lease_seconds=self._lease_seconds
        )
        if lease is None:
            return False
        loop = self._repository.loop_for_step(lease)
        owner_id = self._repository.loop_owner(lease.loop_id)
        for snapshot in self._repository.authorization_snapshots_for_loop(
            lease.loop_id
        ):
            decision = self._runtime_registry.authorize_task(
                owner_id, snapshot.agent_id, snapshot.capability_version
            )
            if snapshot.allowed and decision.reason_code == "authorization_changed":
                self._repository.fail_with_platform_summary(
                    lease.loop_id, "authorization_changed"
                )
                return True
        messages = self._context_policy.build_brain_context(
            self._repository.reconstruct_messages(lease.loop_id)
        ).messages
        owned_task_ids = self._repository.task_ids_for_loop(lease.loop_id)
        active_task_ids = self._repository.active_session_task_ids(lease.loop_id)
        forced = (
            loop.task_count >= loop.max_tasks
            or lease.step_seq >= loop.max_steps
            or (
                loop.active_deadline_at is not None
                and loop.active_deadline_at <= datetime.now(timezone.utc)
            )
        )
        request = self._request_builder.build(
            messages=messages,
            step_seq=lease.step_seq,
            system_prompt=self._system_prompt.text,
            tool_choice=(
                {"type": "tool", "name": "submit_answer"} if forced else None
            ),
            budget_notice=(
                "执行预算已到达上限。请立即使用 submit_answer 提交已有结果，"
                "并明确说明未完成部分。"
                if forced
                else None
            ),
        )
        try:
            response = self._model.complete(request)
        except ProviderRefused:
            self._repository.fail_with_platform_summary(
                lease.loop_id, "provider_refused"
            )
            return True
        except BrainModelError:
            self._repository.fail_with_platform_summary(
                lease.loop_id,
                "forced_submission_failed" if forced else "provider_failed",
            )
            return True
        try:
            batch = parse_tool_batch(
                response.content_blocks,
                ToolLimits(
                    max_parallel_tasks=max(
                        1, min(4, loop.max_tasks - loop.task_count)
                    ),
                    allowed_task_ids=owned_task_ids,
                    active_task_ids=active_task_ids,
                ),
            )
            if forced and batch.kind != "submit_answer":
                raise ProtocolViolation("mixed_tool_batch")
        except ProtocolViolation:
            if forced:
                self._repository.fail_with_platform_summary(
                    lease.loop_id, "forced_submission_failed"
                )
                return True
            if not self._repository.record_protocol_retry(lease.loop_id):
                self._repository.fail_with_platform_summary(
                    lease.loop_id, "protocol_violation_after_retry"
                )
                return True
            correction = self._request_builder.build(
                messages=messages,
                step_seq=lease.step_seq,
                system_prompt=self._system_prompt.text,
                budget_notice=(
                    "上一响应违反工具协议。不要输出自由文本；本次必须只调用一个"
                    "合法工具。"
                ),
            )
            try:
                response = self._model.complete(correction)
                batch = parse_tool_batch(
                    response.content_blocks,
                    ToolLimits(
                        max_parallel_tasks=max(
                            1, min(4, loop.max_tasks - loop.task_count)
                        ),
                        allowed_task_ids=owned_task_ids,
                        active_task_ids=active_task_ids,
                    ),
                )
            except ProviderRefused:
                self._repository.fail_with_platform_summary(
                    lease.loop_id, "provider_refused"
                )
                return True
            except (BrainModelError, ProtocolViolation):
                self._repository.fail_with_platform_summary(
                    lease.loop_id, "protocol_violation_after_retry"
                )
                return True
        immediate: list[ImmediateToolResult] = []
        task_specs: list[TaskDispatchSpec] = []
        if batch.kind == "list_agents":
            snapshots = self._runtime_registry.list_for_user(owner_id)
            immediate.append(
                ImmediateToolResult(
                    0,
                    {
                        "status": "completed",
                        "agents": [_public_value(item) for item in snapshots],
                    },
                )
            )
        elif batch.kind == "delegate_tasks":
            for parsed in batch.calls:
                if not parsed.accepted or not isinstance(parsed.call, DelegateTaskCall):
                    continue
                now = datetime.now(timezone.utc)
                if (
                    loop.active_deadline_at is not None
                    and loop.active_deadline_at <= now + timedelta(seconds=1)
                ):
                    immediate.append(
                        ImmediateToolResult(
                            parsed.tool_index,
                            {
                                "status": "failed",
                                "reason": "deadline_insufficient",
                            },
                        )
                    )
                    continue
                decision = self._runtime_registry.authorize_task(
                    owner_id, parsed.call.agent_id, 1
                )
                if not decision.allowed:
                    immediate.append(
                        ImmediateToolResult(
                            parsed.tool_index,
                            {"status": "unavailable", "reason": decision.reason_code},
                        )
                    )
                    continue
                snapshot_id = self._repository.create_authorization_snapshot(
                    internal_user_id=owner_id,
                    agent_id=parsed.call.agent_id,
                    allowed=True,
                    grant_ids=tuple(decision.grant_ids),
                    directory_generation_id=decision.directory_generation_id,
                    capability_version=decision.capability_version,
                    effective_decision_hash=decision.effective_decision_hash,
                )
                task_specs.append(
                    TaskDispatchSpec(
                        tool_index=parsed.tool_index,
                        adapter_kind=decision.adapter_kind,
                        capability_version=decision.capability_version,
                        authorization_snapshot_id=snapshot_id,
                        effective_deadline_at=min(
                            now + timedelta(seconds=300),
                            loop.active_deadline_at
                            or now + timedelta(seconds=300),
                        ),
                        task_context={
                            "objective": parsed.call.objective,
                            **json.loads(
                                self._context_policy.build_task_context(
                                    parsed.call
                                ).serialized
                            ),
                        },
                    )
                )
        elif batch.kind == "submit_answer":
            call = batch.calls[0].call
            assert isinstance(call, SubmitAnswerCall)
            immediate.append(
                ImmediateToolResult(0, {"status": "accepted"})
            )
        self._repository.commit_model_step(
            lease.loop_id,
            lease.step_seq,
            self._worker_id,
            ModelStepCommit(
                provider_request_id=response.provider_request_id,
                content_blocks=response.content_blocks,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
                cache_usage={
                    "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
                    "cache_read_input_tokens": response.usage.cache_read_input_tokens,
                },
                stop_reason=response.stop_reason,
                batch=batch,
                task_specs=tuple(task_specs),
                immediate_results=tuple(immediate),
            ),
        )
        return True

    def scan_settled_batches(self) -> int:
        settled = self._repository.settle_ready_batches(limit=100)
        if settled and self._model is not None:
            self.advance_one()
        return settled

    def reconcile_cancellations(self) -> int:
        for selected in self._repository.cancellation_tasks(limit=100):
            adapter = self._adapters.require(selected.adapter_kind)
            if adapter.supports_cancellation:
                adapter.request_cancel(
                    AdapterTask(
                        task_id=selected.task_id,
                        loop_id=selected.loop_id,
                        agent_id=selected.agent_id,
                        context=selected.context,
                        effective_deadline_at=selected.effective_deadline_at,
                    )
                )
            result = NormalizedTaskResult(
                status="cancelled",
                summary="用户已停止本轮任务。",
                deliverables=(),
                evidence=(),
                limitations=("任务结果未继续交付。",),
                attachment_refs=(),
            )
            self._repository.append_task_event(
                AgentTaskEventInput(
                    task_id=selected.task_id,
                    seq=selected.next_event_seq,
                    event_type="agent.cancelled",
                    created_at=datetime.now(timezone.utc),
                    payload={"status": "cancelled"},
                    terminal_status="cancelled",
                    result=result,
                )
            )
        return self._repository.terminalize_requested_cancellations(limit=100)

    def dispatch_one(self) -> bool:
        lease = self._repository.lease_task_delivery(
            self._worker_id, lease_seconds=self._lease_seconds
        )
        if lease is None:
            return False
        adapter = self._adapters.require(lease.adapter_kind)
        task = AdapterTask(
            task_id=lease.task_id,
            loop_id=lease.loop_id,
            agent_id=lease.agent_id,
            context=lease.context,
            effective_deadline_at=lease.effective_deadline_at,
            requester_subject=lease.requester_subject,
        )
        delivery = AdapterDelivery(
            delivery_id=lease.delivery_id,
            attempt=lease.attempt,
            idempotency_key=lease.idempotency_key,
            delivery_kind=lease.delivery_kind,
            source_message_seq=lease.source_message_seq,
        )
        remote_child_id = (
            str(lease.adapter_session_ref.get("child_session_id"))
            if lease.adapter_session_ref
            and lease.adapter_session_ref.get("child_session_id")
            else lease.child_session_id
        )
        if lease.delivery_kind == "initial":
            receipt = adapter.start_session(task, delivery)
            if not receipt.accepted:
                self._repository.complete_leased_delivery(lease)
                self._collaboration.append_task_event_and_wake(
                    AgentTaskPublicEventInput(
                        task_id=lease.task_id,
                        seq=1,
                        event_type="failed",
                        payload={"status": "unavailable"},
                        created_at=datetime.now(timezone.utc),
                    )
                )
                return True
            self._repository.bind_adapter_session_ref(
                lease.task_id,
                {
                    "child_session_id": receipt.child_session_id,
                    "external_run_id": (
                        str(receipt.external_run_id)
                        if receipt.external_run_id is not None
                        else None
                    ),
                },
            )
            self._repository.mark_delivery_dispatched(lease)
        elif lease.delivery_kind == "followup":
            if lease.source_message_seq is None or lease.message_text is None:
                raise RuntimeError("Follow-up delivery is incomplete")
            adapter.send_message(
                remote_child_id,
                AdapterMessage(
                    seq=lease.source_message_seq,
                    text=lease.message_text,
                    created_at=datetime.now(timezone.utc),
                ),
                delivery,
            )
            self._repository.complete_leased_delivery(lease)
        else:
            adapter.request_stop(
                remote_child_id,
                "Agent 大脑已请求停止当前专业任务。",
                delivery,
            )
            self._repository.complete_leased_delivery(lease)
        return True

    def reconcile_one(self) -> bool:
        selected = self._repository.next_adapter_session_poll()
        if selected is None:
            return False
        adapter = self._adapters.require(selected.adapter_kind)
        remote_child_id = (
            str(selected.adapter_session_ref.get("child_session_id"))
            if selected.adapter_session_ref
            and selected.adapter_session_ref.get("child_session_id")
            else selected.child_session_id
        )
        events = adapter.read_events(
            remote_child_id, after=selected.after_event_seq
        )
        changed = False
        saw_result = False
        for event in events:
            event_type = event.kind
            if event.kind == "work_update" and event.payload.get("kind") == "finding":
                event_type = "finding"
            elif event.kind == "error":
                event_type = "failed"
            outcome = self._collaboration.append_task_event_and_wake(
                AgentTaskPublicEventInput(
                    task_id=selected.task_id,
                    seq=event.seq,
                    event_type=event_type,
                    payload=dict(event.payload),
                    created_at=event.created_at,
                )
            )
            changed = changed or not outcome.replayed
            saw_result = saw_result or event_type in {
                "result",
                "failed",
                "timeout",
                "cancelled",
            }
        if saw_result:
            self._repository.complete_initial_delivery_after_events(
                selected.task_id
            )
        self._repository.touch_adapter_session(selected.task_id)
        return changed

    def reconcile_adapter_tasks(self, adapter_kind: str) -> int:
        adapter = self._adapters.require(adapter_kind)
        reconcile = getattr(adapter, "reconcile", None)
        if not callable(reconcile):
            return 0
        changed = 0
        for selected in self._repository.adapter_reconciliation_tasks(
            adapter_kind, limit=100
        ):
            now = datetime.now(timezone.utc)
            deadline = selected.effective_deadline_at
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if deadline <= now:
                result = NormalizedTaskResult(
                    status="timed_out",
                    summary="专业 Agent 未在本轮截止时间内返回结果。",
                    deliverables=(),
                    evidence=(),
                    limitations=("该子任务已超时，Agent 大脑将基于已有结果继续。",),
                    attachment_refs=(),
                )
                changed += int(
                    self._repository.append_task_event(
                        AgentTaskEventInput(
                            task_id=selected.task_id,
                            seq=selected.next_event_seq,
                            event_type="agent.timed_out",
                            created_at=now,
                            payload={"status": "timed_out"},
                            terminal_status="timed_out",
                            result=result,
                        )
                    )
                )
                self._repository.complete_reconciled_delivery(
                    selected.task_id, selected.loop_id
                )
                continue
            task = AdapterTask(
                task_id=selected.task_id,
                loop_id=selected.loop_id,
                agent_id=selected.agent_id,
                context=selected.context,
                effective_deadline_at=selected.effective_deadline_at,
            )
            receipt = reconcile(task, next_event_seq=selected.next_event_seq)
            for event in receipt.events:
                changed += int(self._repository.append_task_event(event))
            if receipt.terminal:
                self._repository.complete_reconciled_delivery(
                    selected.task_id, selected.loop_id
                )
        return changed


def _public_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise ValueError("runtime Agent snapshot invalid")
