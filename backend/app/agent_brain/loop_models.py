from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class BrainLoopStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_AGENTS = "waiting_agents"
    WAITING_USER = "waiting_user"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class BrainStepStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    REQUESTING_MODEL = "requesting_model"
    WAITING_TOOL_RESULTS = "waiting_tool_results"
    COMPLETED = "completed"
    FAILED = "failed"


class BrainToolCallStatus(StrEnum):
    ACCEPTED = "accepted"
    WAITING_RESULT = "waiting_result"
    RESULT_READY = "result_ready"
    CONSUMED = "consumed"
    FAILED = "failed"


class AgentTaskStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


class AdapterDeliveryStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class NormalizedTaskResult(BaseModel):
    """Bounded model-visible result produced by every professional Adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal[
        "completed", "failed", "cancelled", "timed_out", "unavailable"
    ]
    summary: str
    deliverables: tuple[str, ...]
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    attachment_refs: tuple[UUID, ...]

    @field_validator("summary")
    @classmethod
    def _bounded_summary(cls, value: str) -> str:
        _require_utf8_text(value, minimum=1, maximum=32768)
        return value

    @field_validator("deliverables", "evidence", "limitations")
    @classmethod
    def _bounded_text_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 64:
            raise ValueError("too many task result items")
        for member in value:
            _require_utf8_text(member, minimum=1, maximum=8192)
        return value

    @field_validator("attachment_refs")
    @classmethod
    def _bounded_attachment_refs(
        cls, value: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        if len(value) > 32 or len(set(value)) != len(value):
            raise ValueError("task result attachment references invalid")
        return value


def _require_utf8_text(value: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not str:
        raise TypeError("text value required")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as error:
        raise ValueError("text must be valid UTF-8") from error
    if not value.strip() or not minimum <= size <= maximum:
        raise ValueError("text size invalid")
    return size


@dataclass(frozen=True, slots=True)
class AuthorizationSnapshot:
    authorization_snapshot_id: UUID
    internal_user_id: UUID
    agent_id: str
    allowed: bool
    capability_version: int
    effective_decision_hash: bytes
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class BrainLoopRecord:
    loop_id: UUID
    conversation_id: UUID
    turn_id: UUID
    status: BrainLoopStatus
    step_count: int
    task_count: int
    max_steps: int
    max_tasks: int
    active_budget_ms: int
    active_elapsed_ms: int
    row_version: int
    created_at: datetime
    updated_at: datetime
    active_deadline_at: datetime | None = None
    waiting_user_expires_at: datetime | None = None
    intervention_expires_at: datetime | None = None
    reason_code: str | None = None
    fallback_used: bool = False


@dataclass(frozen=True, slots=True)
class BrainStepRecord:
    step_id: UUID
    loop_id: UUID
    step_seq: int
    status: BrainStepStatus
    attempt: int
    created_at: datetime
    updated_at: datetime
    lease_worker_id: str | None = None
    lease_expires_at: datetime | None = None
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTaskRecord:
    task_id: UUID
    loop_id: UUID
    brain_tool_call_id: UUID
    agent_id: str
    adapter_kind: str
    capability_version: int
    authorization_snapshot_id: UUID
    status: AgentTaskStatus
    effective_deadline_at: datetime
    cancel_requested: bool
    row_version: int
    dispatched_at: datetime | None = None
    active_elapsed_ms: int = 0
    terminal_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterDeliveryRecord:
    delivery_id: UUID
    task_id: UUID
    adapter_kind: str
    attempt: int
    status: AdapterDeliveryStatus
    idempotency_key: str
    lease_worker_id: str | None = None
    lease_expires_at: datetime | None = None
