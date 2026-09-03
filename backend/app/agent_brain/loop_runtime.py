from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.agent_brain.adapters.base import (
    AdapterDelivery,
    AdapterMessage,
    AdapterRegistry,
    AdapterTask,
    AgentEventProtocolError,
)
from app.agent_brain.agent_roster import ROSTER_UNAVAILABLE, render_agent_roster
from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.agent_brain.collaboration_models import (
    AgentTaskPublicEventInput,
)
from app.agent_brain.collaboration_models import (
    BrainThinkingDelta as StoredThinkingDelta,
)
from app.agent_brain.context_policy import BrainContextPolicy
from app.agent_brain.loop_models import NormalizedTaskResult
from app.agent_brain.loop_repository import (
    AgentTaskEventInput,
    BrainLoopRepository,
    BrainRepositoryConflict,
    BrainRepositoryError,
    ImmediateToolResult,
    ModelStepCommit,
    TaskDispatchSpec,
)
from app.agent_brain.model_adapter import (
    BrainModelAdapter,
    BrainModelError,
    BrainRequestBuilder,
    ProviderRefused,
    ThinkingDelta,
)
from app.agent_brain.prompt import BrainSystemPrompt
from app.agent_brain.tool_protocol import (
    DelegateTaskCall,
    ProtocolViolation,
    SubmitAnswerCall,
    ToolLimits,
    parse_tool_batch,
)
from app.execution_relay.models import (
    OutputWriteGrantPayload,
    TaskAttachmentGrantPayload,
)

_LEASE_RENEW_INTERVAL_SECONDS = 15.0

# Per-task window. Reading it from the authorized capability card instead is the
# Platform task brief's §12 item; the chain-depth formula below already has the
# right shape for it.
_TASK_SECONDS = 300


def _chain_depth(batch, tool_index: int) -> int:
    """Return the longest declared dependency path ending at this call.

    A dependent task is not dispatched until its upstream finishes, so its window
    has to cover the chain ahead of it. Without this a task deep in a chain would
    burn its own deadline while still queued and be reaped before it ever ran --
    a failure mode that data edges introduce and must therefore answer for.

    depends_on may only reference an earlier position, so one forward pass in
    declaration order is a valid topological order.
    """

    depths: dict[int, int] = {}
    for call in batch.calls:
        upstream = getattr(call.call, "depends_on", ())
        depths[call.tool_index] = max(
            (depths.get(index, 0) + 1 for index in upstream), default=0
        )
    return depths.get(tool_index, 0)


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
        action_commands: object | None = None,
        attachment_grants: object | None = None,
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
        self._action_commands = action_commands
        self._attachment_grants = attachment_grants

    def expire_actions(self) -> int:
        expire = getattr(self._action_commands, "expire", None)
        return expire(limit=100) if callable(expire) else 0

    def advance_one(self) -> bool:
        if self._model is None or not hasattr(self._model, "complete"):
            return False
        lease = self._repository.lease_step(
            self._worker_id, lease_seconds=self._lease_seconds
        )
        if lease is None:
            return False
        loop = self._repository.loop_for_step(lease)
        self._collaboration.claim_intervention(lease.loop_id, lease.step_id)
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
        persisted_messages = self._repository.reconstruct_messages(lease.loop_id)
        messages = self._context_policy.build_brain_context(persisted_messages).messages
        active_attachment_ids = self._repository.active_attachment_ids_for_loop(
            lease.loop_id
        )
        ready_artifact_ids = tuple(
            getattr(
                self._repository,
                "ready_artifact_ids_for_loop",
                lambda _loop_id: (),
            )(lease.loop_id)
        )
        allowed_attachment_refs = frozenset(
            (*active_attachment_ids, *ready_artifact_ids)
        )
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
        pending_actions = self._repository.pending_action_ids(lease.loop_id)
        if forced and pending_actions:
            intervention_expires_at = self._repository.earliest_action_expiry(
                lease.loop_id
            )
            if (
                intervention_expires_at is not None
                and self._repository.pause_for_pending_actions(
                    lease.loop_id,
                    lease.step_id,
                    self._worker_id,
                    intervention_expires_at=intervention_expires_at,
                )
            ):
                return True
            # The Action may have resolved between the read and the atomic pause.
            pending_actions = self._repository.pending_action_ids(lease.loop_id)
        request = self._request_builder.build(
            messages=messages,
            step_seq=lease.step_seq,
            system_prompt=self._system_prompt.text,
            agent_roster=self._agent_roster(owner_id),
            effort=self._select_effort(
                forced=forced,
                has_settled_task=self._repository.has_settled_task(lease.loop_id),
            ),
            tool_choice=({"type": "tool", "name": "submit_answer"} if forced else None),
            budget_notice=(
                "执行预算已到达上限。请立即使用 submit_answer 提交已有结果，"
                "并明确说明未完成部分。"
                if forced
                else None
            ),
        )
        thinking_blocks: set[int] = set()
        last_renewed = time.monotonic()

        def persist_thinking(delta: ThinkingDelta) -> None:
            nonlocal last_renewed
            thinking_blocks.add(delta.block_index)
            self._collaboration.append_thinking_delta(
                StoredThinkingDelta(
                    step_id=lease.step_id,
                    block_index=delta.block_index,
                    delta_seq=delta.delta_seq,
                    text=delta.text,
                    provider_run_ref=delta.provider_run_ref,
                )
            )
            elapsed = time.monotonic() - last_renewed
            if elapsed >= _LEASE_RENEW_INTERVAL_SECONDS:
                last_renewed = time.monotonic()
                # Best effort: the lease already outlives a normal Step, so a failed
                # renewal must not abort a paid model call. A stolen Step is caught
                # by commit_model_step's lease_worker_id check.
                try:
                    self._repository.renew_step_lease(
                        lease.step_id,
                        self._worker_id,
                        lease_seconds=self._lease_seconds,
                    )
                except BrainRepositoryError:
                    pass

        try:
            response = self._model.complete(request, on_thinking_delta=persist_thinking)
        except ProviderRefused:
            self._finalize_thinking(lease.step_id, thinking_blocks, interrupted=True)
            self._repository.fail_with_platform_summary(
                lease.loop_id, "provider_refused"
            )
            return True
        except BrainModelError:
            self._finalize_thinking(lease.step_id, thinking_blocks, interrupted=True)
            self._repository.fail_with_platform_summary(
                lease.loop_id,
                "forced_submission_failed" if forced else "provider_failed",
            )
            return True
        try:
            batch = parse_tool_batch(
                response.content_blocks,
                ToolLimits(
                    max_parallel_tasks=max(1, min(4, loop.max_tasks - loop.task_count)),
                    allowed_task_ids=owned_task_ids,
                    active_task_ids=active_task_ids,
                    allowed_attachment_refs=allowed_attachment_refs,
                ),
            )
            if forced and batch.kind != "submit_answer":
                raise ProtocolViolation("mixed_tool_batch")
        except ProtocolViolation:
            if forced:
                self._finalize_thinking(
                    lease.step_id, thinking_blocks, interrupted=True
                )
                self._repository.fail_with_platform_summary(
                    lease.loop_id, "forced_submission_failed"
                )
                return True
            if not self._repository.record_protocol_retry(lease.loop_id):
                self._finalize_thinking(
                    lease.step_id, thinking_blocks, interrupted=True
                )
                self._repository.fail_with_platform_summary(
                    lease.loop_id, "protocol_violation_after_retry"
                )
                return True
            self._finalize_thinking(lease.step_id, thinking_blocks, interrupted=True)
            thinking_blocks.clear()
            correction = self._request_builder.build(
                messages=messages,
                step_seq=lease.step_seq,
                system_prompt=self._system_prompt.text,
                agent_roster=self._agent_roster(owner_id),
                effort=self._request_builder.manifest.thinking_effort_routing,
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
                        allowed_attachment_refs=allowed_attachment_refs,
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
        self._finalize_thinking(lease.step_id, thinking_blocks, interrupted=False)
        immediate: list[ImmediateToolResult] = []
        task_specs: list[TaskDispatchSpec] = []
        if batch.kind == "list_agents":
            try:
                snapshots = self._runtime_registry.list_for_user(owner_id)
            except AgentUseAuthorizationUnavailable:
                snapshots = None
            immediate.append(
                ImmediateToolResult(
                    0,
                    {
                        "status": "failed",
                        "reason": "authorization_unavailable",
                    }
                    if snapshots is None
                    else {
                        "status": "completed",
                        "agents": [_public_value(item) for item in snapshots],
                    },
                )
            )
        elif batch.kind == "delegate_tasks":
            capability_mismatches = {
                parsed.call.agent_id: _consecutive_capability_mismatches(
                    persisted_messages, parsed.call.agent_id
                )
                for parsed in batch.calls
                if parsed.accepted and isinstance(parsed.call, DelegateTaskCall)
            }
            pool_admissions: dict[str, int] = {}
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
                    capability_mismatches[parsed.call.agent_id] = 0
                    continue
                decision = self._runtime_registry.authorize_task(
                    owner_id,
                    parsed.call.agent_id,
                    parsed.call.capability_version,
                )
                if not decision.allowed:
                    if decision.reason_code == "capability_changed":
                        unstable = capability_mismatches[parsed.call.agent_id] >= 1
                        capability_mismatches[parsed.call.agent_id] += 1
                        immediate.append(
                            ImmediateToolResult(
                                parsed.tool_index,
                                {
                                    "status": "rejected",
                                    "reason": (
                                        "capability_version_unstable"
                                        if unstable
                                        else "capability_changed"
                                    ),
                                    "current_capability_version": (
                                        decision.capability_version
                                    ),
                                    "must_call_list_agents": not unstable,
                                },
                            )
                        )
                        continue
                    capability_mismatches[parsed.call.agent_id] = 0
                    immediate.append(
                        ImmediateToolResult(
                            parsed.tool_index,
                            {"status": "unavailable", "reason": decision.reason_code},
                        )
                    )
                    continue
                capability_mismatches[parsed.call.agent_id] = 0
                # Same-pool tasks contend for one executor. A chained task never runs
                # beside its upstream, so only unchained calls are counted -- which is
                # exactly the shape the rejection hint asks the Brain to switch to.
                if not parsed.call.depends_on and decision.execution_pool is not None:
                    admitted = pool_admissions.get(decision.execution_pool, 0)
                    if admitted >= (decision.pool_concurrency or 1):
                        immediate.append(
                            ImmediateToolResult(
                                parsed.tool_index,
                                {
                                    "status": "rejected",
                                    "reason": "pool_saturated",
                                    "execution_pool": decision.execution_pool,
                                    "pool_concurrency": decision.pool_concurrency,
                                    "hint": (
                                        "同池 Agent 共用一个执行器；"
                                        "请用 depends_on 串接而不是并行派发。"
                                    ),
                                },
                            )
                        )
                        continue
                    pool_admissions[decision.execution_pool] = admitted + 1
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
                            now
                            + timedelta(
                                seconds=_TASK_SECONDS
                                * (1 + _chain_depth(batch, parsed.tool_index))
                            ),
                            loop.active_deadline_at
                            or now + timedelta(seconds=_TASK_SECONDS),
                        ),
                        task_context={
                            "objective": parsed.call.objective,
                            **json.loads(
                                self._context_policy.build_task_context(
                                    parsed.call,
                                    active_attachment_ids=active_attachment_ids,
                                ).serialized
                            ),
                        },
                    )
                )
        elif batch.kind == "submit_answer":
            call = batch.calls[0].call
            assert isinstance(call, SubmitAnswerCall)
            pending_actions = self._repository.pending_action_ids(lease.loop_id)
            invalid_artifact_refs = not set(call.attachment_refs).issubset(
                ready_artifact_ids
            )
            immediate.append(
                ImmediateToolResult(
                    0,
                    (
                        {
                            "status": "rejected",
                            "reason": "pending_action_requires_resolution",
                            "required_next_action": "await_agent_events",
                        }
                        if pending_actions
                        else {
                            "status": "rejected",
                            "reason": "result_file_not_ready",
                            "required_next_action": "await_agent_events",
                        }
                        if invalid_artifact_refs
                        else {"status": "accepted"}
                    ),
                )
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

    def _agent_roster(self, owner_id) -> str:
        """Render the caller's delegatable Agents for the cached system prefix.

        Without this the Brain has no way to learn that any Agent exists except by
        spending a whole model Step on list_agents, which is why a bare "what can
        you do" question used to cost two Steps and could exhaust the Turn budget.
        """

        try:
            return render_agent_roster(self._runtime_registry.roster_for_user(owner_id))
        except AgentUseAuthorizationUnavailable:
            return ROSTER_UNAVAILABLE

    def _select_effort(self, *, forced: bool, has_settled_task: bool) -> str:
        """Choose reasoning effort from persisted state only, so replays reproduce it.

        A forced close-out only writes up what is already in context, and a Step with
        no settled Agent result is routing rather than synthesis; both take the
        cheaper routing tier. Every Step used the synthesis tier before this, which
        let one Turn spend its whole active budget on the Brain's own thinking before
        any Agent finished.
        """

        manifest = self._request_builder.manifest
        if not forced and has_settled_task:
            return manifest.thinking_effort
        return manifest.thinking_effort_routing

    def _finalize_thinking(
        self, step_id, block_indexes: set[int], *, interrupted: bool
    ) -> None:
        for block_index in sorted(block_indexes):
            self._collaboration.finalize_thinking_summary(
                step_id, block_index, interrupted=interrupted
            )

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
        input_attachment_grants: tuple[TaskAttachmentGrantPayload, ...] = ()
        output_write_grant: OutputWriteGrantPayload | None = None
        if (
            self._attachment_grants is not None
            and adapter.capabilities.supports_attachments
        ):
            attachment_ids: list[UUID] = []
            raw_attachment_refs = lease.context.get("attachment_refs", [])
            if isinstance(raw_attachment_refs, list):
                for raw_attachment_id in raw_attachment_refs:
                    try:
                        attachment_id = UUID(raw_attachment_id)
                    except (AttributeError, TypeError, ValueError):
                        continue
                    if attachment_id not in attachment_ids:
                        attachment_ids.append(attachment_id)
            input_attachment_grants = tuple(
                TaskAttachmentGrantPayload.model_validate(
                    asdict(
                        self._attachment_grants.issue_attachment(
                            lease.task_id,
                            attachment_id,
                            lease.agent_id,
                            expires_at=lease.effective_deadline_at,
                        )
                    )
                )
                for attachment_id in attachment_ids
            )
            output_write_grant = OutputWriteGrantPayload.model_validate(
                asdict(
                    self._attachment_grants.issue_output(
                        lease.task_id,
                        lease.agent_id,
                        expires_at=lease.effective_deadline_at,
                    )
                )
            )
        task = AdapterTask(
            task_id=lease.task_id,
            loop_id=lease.loop_id,
            conversation_id=lease.conversation_id,
            turn_id=lease.turn_id,
            agent_id=lease.agent_id,
            capability_version=lease.capability_version,
            context=lease.context,
            effective_deadline_at=lease.effective_deadline_at,
            requester_subject=lease.requester_subject,
            input_attachment_grants=input_attachment_grants,
            output_write_grant=output_write_grant,
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
                task=task,
            )
            self._repository.complete_leased_delivery(lease)
        else:
            adapter.request_stop(
                remote_child_id,
                "Agent 大脑已请求停止当前专业任务。",
                delivery,
                task=task,
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
        try:
            task = AdapterTask(
                task_id=selected.task_id,
                loop_id=selected.loop_id,
                conversation_id=selected.conversation_id,
                turn_id=selected.turn_id,
                agent_id=selected.agent_id,
                capability_version=selected.capability_version,
                context={},
                effective_deadline_at=(
                    selected.effective_deadline_at or datetime.now(timezone.utc)
                ),
                requester_subject=selected.requester_subject,
            )
            events = adapter.read_events(
                remote_child_id, after=selected.after_event_seq, task=task
            )
        except AgentEventProtocolError:
            self._repository.fail_agent_task_protocol(selected.task_id)
            return True
        expected_seq = selected.after_event_seq + 1
        if any(event.seq != expected_seq + index for index, event in enumerate(events)):
            self._repository.fail_agent_task_protocol(selected.task_id)
            return True
        changed = False
        saw_result = False
        try:
            for event in events:
                event_type = event.kind
                if (
                    event.kind == "work_update"
                    and event.payload.get("kind") == "finding"
                ):
                    event_type = "finding"
                elif event.kind == "error":
                    event_type = "failed"
                if event_type == "result":
                    nested_result = event.payload.get("result")
                    artifact_values = (
                        nested_result.get("artifacts")
                        if isinstance(nested_result, dict)
                        and nested_result.get("contractVersion")
                        == "core_chat_collaboration_v4"
                        else None
                    )
                    if isinstance(artifact_values, list) and artifact_values:
                        try:
                            artifact_state = (
                                self._attachment_grants.classify_result_artifacts(
                                    selected.task_id,
                                    selected.agent_id,
                                    tuple(
                                        dict(value)
                                        for value in artifact_values
                                        if isinstance(value, dict)
                                    ),
                                )
                                if self._attachment_grants is not None
                                else "invalid"
                            )
                        except Exception:
                            artifact_state = "invalid"
                        if artifact_state == "pending":
                            self._repository.touch_adapter_session(selected.task_id)
                            return changed
                        if artifact_state != "ready":
                            self._repository.fail_agent_task_protocol(
                                selected.task_id
                            )
                            return True
                outcome = self._collaboration.append_task_event_and_wake(
                    AgentTaskPublicEventInput(
                        task_id=selected.task_id,
                        seq=event.seq,
                        event_type=event_type,
                        payload={
                            **dict(event.payload),
                            "source": event.source,
                            "source_ref": event.source_ref,
                        },
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
        except (BrainRepositoryConflict, ValueError):
            self._repository.fail_agent_task_protocol(selected.task_id)
            return True
        if saw_result:
            self._repository.complete_initial_delivery_after_events(selected.task_id)
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
        public = asdict(value)
        for field in (
            "grant_ids",
            "directory_generation_id",
            "effective_decision_hash",
        ):
            public.pop(field, None)
        sampled_at = public.get("health_sampled_at")
        if isinstance(sampled_at, datetime):
            public["health_sampled_at"] = sampled_at.isoformat()
        return public
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise ValueError("runtime Agent snapshot invalid")


def _consecutive_capability_mismatches(
    messages: tuple[dict[str, object], ...], agent_id: str
) -> int:
    delegated_agents: dict[str, str] = {}
    mismatches = 0
    for message in messages:
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, list):
            for block in content:
                if (
                    not isinstance(block, dict)
                    or block.get("type") != "tool_use"
                    or block.get("name") != "delegate_task"
                    or not isinstance(block.get("id"), str)
                    or not isinstance(block.get("input"), dict)
                ):
                    continue
                delegated_agent = block["input"].get("agent_id")
                if isinstance(delegated_agent, str):
                    delegated_agents[block["id"]] = delegated_agent
        elif message.get("role") == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = block.get("tool_use_id")
                if delegated_agents.get(tool_use_id) != agent_id:
                    continue
                raw_result = block.get("content")
                if not isinstance(raw_result, str):
                    continue
                try:
                    result = json.loads(raw_result)
                except (TypeError, ValueError):
                    continue
                if not isinstance(result, dict):
                    continue
                if result.get("reason") == "capability_changed":
                    mismatches += 1
                elif result.get("reason") == "capability_version_unstable":
                    mismatches = 2
                else:
                    mismatches = 0
    return mismatches
