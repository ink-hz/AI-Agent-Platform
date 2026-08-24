from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from app.agent_brain.adapters.base import (
    AdapterDelivery,
    AdapterRegistry,
    AdapterTask,
)
from app.agent_brain.loop_repository import (
    BrainLoopRepository,
    ImmediateToolResult,
    ModelStepCommit,
    TaskDispatchSpec,
)
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
    ) -> None:
        self._repository = repository
        self._model = model
        self._request_builder = request_builder
        self._system_prompt = system_prompt
        self._runtime_registry = runtime_registry
        self._adapters = adapters
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

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
        messages = self._repository.reconstruct_messages(lease.loop_id)
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
                    )
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
                        )
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

    def dispatch_one(self) -> bool:
        lease = self._repository.lease_task_delivery(
            self._worker_id, lease_seconds=self._lease_seconds
        )
        if lease is None:
            return False
        adapter = self._adapters.require(lease.adapter_kind)
        receipt = adapter.dispatch(
            AdapterTask(
                task_id=lease.task_id,
                loop_id=lease.loop_id,
                agent_id=lease.agent_id,
                context=lease.context,
                effective_deadline_at=lease.effective_deadline_at,
            ),
            AdapterDelivery(
                delivery_id=lease.delivery_id,
                attempt=lease.attempt,
                idempotency_key=lease.idempotency_key,
            ),
        )
        self._repository.complete_delivery(lease, receipt.result)
        return True


def _public_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise ValueError("runtime Agent snapshot invalid")
