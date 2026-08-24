from __future__ import annotations

from app.agent_brain.adapters.base import (
    AdapterDelivery,
    AdapterTask,
    AgentAdapter,
    CancelReceipt,
    DispatchReceipt,
)
from app.agent_brain.loop_models import NormalizedTaskResult


class ReferenceAdapter(AgentAdapter):
    """Deterministic, network-free Adapter used to prove runtime durability."""

    supports_cancellation = True

    def dispatch(
        self, task: AdapterTask, delivery: AdapterDelivery
    ) -> DispatchReceipt:
        return DispatchReceipt(
            accepted=True,
            result=NormalizedTaskResult(
                status="completed",
                summary="Reference Agent 已完成确定性链路验证。",
                deliverables=("durable-loop-reference-result",),
                evidence=(f"task_id={task.task_id}",),
                limitations=("该结果仅验证平台执行链路，不代表业务判断。",),
                attachment_refs=(),
            ),
        )

    def request_cancel(self, task: AdapterTask) -> CancelReceipt:
        return CancelReceipt(accepted=True)
