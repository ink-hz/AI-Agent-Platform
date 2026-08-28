from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

AgentTaskMessageSender = Literal["brain", "agent"]
AgentTaskMessageKind = Literal["initial", "followup", "question", "reply", "result"]
AgentTaskPublicEventKind = Literal[
    "thinking_summary",
    "message",
    "work_update",
    "artifact",
    "question",
    "finding",
    "result",
    "failed",
    "timeout",
    "cancelled",
    "input_required",
    "action_required",
]
WaitWakeKind = Literal[
    "question",
    "finding",
    "result",
    "failed",
    "timeout",
    "input_required",
    "action_required",
]

PUBLIC_EVENT_KINDS = frozenset(
    {
        "thinking_summary",
        "message",
        "work_update",
        "artifact",
        "question",
        "finding",
        "result",
        "failed",
        "timeout",
        "cancelled",
        "input_required",
        "action_required",
    }
)
WAIT_WAKE_KINDS = frozenset(
    {
        "question",
        "finding",
        "result",
        "failed",
        "timeout",
        "input_required",
        "action_required",
    }
)


def _require_uuid(value: object, name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} invalid")


def _require_text(value: object, name: str, *, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value.strip()
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{name} invalid")


@dataclass(frozen=True, slots=True)
class AgentTaskMessageInput:
    task_id: UUID
    seq: int
    sender: AgentTaskMessageSender
    message_kind: AgentTaskMessageKind
    text: str = field(repr=False)
    created_at: datetime
    provider_run_ref: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.task_id, "task ID")
        if type(self.seq) is not int or self.seq <= 0:
            raise ValueError("message sequence invalid")
        if self.sender not in {"brain", "agent"}:
            raise ValueError("message sender invalid")
        if self.message_kind not in {"initial", "followup", "question", "reply", "result"}:
            raise ValueError("message kind invalid")
        _require_text(self.text, "message text", maximum_bytes=16 * 1024)
        if not isinstance(self.created_at, datetime):
            raise ValueError("message timestamp invalid")
        if self.provider_run_ref is not None:
            _require_text(
                self.provider_run_ref, "Provider run reference", maximum_bytes=256
            )


@dataclass(frozen=True, slots=True)
class AgentTaskPublicEventInput:
    task_id: UUID
    seq: int
    event_type: AgentTaskPublicEventKind
    payload: Mapping[str, object] = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        _require_uuid(self.task_id, "task ID")
        if type(self.seq) is not int or self.seq <= 0:
            raise ValueError("event sequence invalid")
        if self.event_type not in PUBLIC_EVENT_KINDS:
            raise ValueError("event kind invalid")
        if type(self.payload) is not dict or not self.payload:
            raise ValueError("event payload invalid")
        if not isinstance(self.created_at, datetime):
            raise ValueError("event timestamp invalid")


@dataclass(frozen=True, slots=True)
class BrainThinkingDelta:
    step_id: UUID
    block_index: int
    delta_seq: int
    text: str = field(repr=False)
    provider_run_ref: str

    def __post_init__(self) -> None:
        _require_uuid(self.step_id, "Step ID")
        if type(self.block_index) is not int or self.block_index < 0:
            raise ValueError("thinking block index invalid")
        if type(self.delta_seq) is not int or self.delta_seq <= 0:
            raise ValueError("thinking delta sequence invalid")
        _require_text(self.text, "thinking summary delta", maximum_bytes=16 * 1024)
        _require_text(
            self.provider_run_ref, "Provider run reference", maximum_bytes=256
        )


@dataclass(frozen=True, slots=True)
class WaitSubscriptionSpec:
    tool_call_id: UUID
    loop_id: UUID
    task_ids: tuple[UUID, ...]
    wake_on: tuple[WaitWakeKind, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.tool_call_id, "Tool Call ID")
        _require_uuid(self.loop_id, "Loop ID")
        if (
            type(self.task_ids) is not tuple
            or not 1 <= len(self.task_ids) <= 8
            or any(not isinstance(task_id, UUID) for task_id in self.task_ids)
            or len(set(self.task_ids)) != len(self.task_ids)
        ):
            raise ValueError("wait task IDs invalid")
        if (
            type(self.wake_on) is not tuple
            or not 1 <= len(self.wake_on) <= len(WAIT_WAKE_KINDS)
            or any(kind not in WAIT_WAKE_KINDS for kind in self.wake_on)
            or len(set(self.wake_on)) != len(self.wake_on)
        ):
            raise ValueError("wait event kinds invalid")


@dataclass(frozen=True, slots=True)
class AgentTaskSessionRecord:
    task_id: UUID
    child_session_id: str
    adapter_kind: str
    adapter_session_ref: Mapping[str, object] | None = field(repr=False)
    capability_snapshot: Mapping[str, object] = field(repr=False)
    status: Literal["active", "completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class TaskMessageAppendResult:
    input: AgentTaskMessageInput = field(repr=False)
    replayed: bool


@dataclass(frozen=True, slots=True)
class AgentTaskMessageRecord:
    task_id: UUID
    seq: int
    sender: AgentTaskMessageSender
    message_kind: AgentTaskMessageKind
    text: str = field(repr=False)
    created_at: datetime
    provider_run_ref: str | None


@dataclass(frozen=True, slots=True)
class AgentTaskPublicEventRecord:
    task_id: UUID
    seq: int
    event_type: str
    payload: Mapping[str, object] = field(repr=False)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EventWakeResult:
    replayed: bool
    woken_wait_id: UUID | None
    events: tuple[AgentTaskPublicEventRecord, ...]
    queued_step_id: UUID | None


@dataclass(frozen=True, slots=True)
class WaitSubscriptionRecord:
    wait_id: UUID
    tool_call_id: UUID
    loop_id: UUID
    task_ids: tuple[UUID, ...]
    wake_on: tuple[str, ...]
    status: Literal["active", "triggered", "cancelled", "expired"]


@dataclass(frozen=True, slots=True)
class WaitSettlementResult:
    settled: bool
    source: Literal["post_commit", "event_append", "reaper"]
    events: tuple[AgentTaskPublicEventRecord, ...]
    serialization_retries: int
    woken_wait_id: UUID | None = None
    queued_step_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class BrainThinkingSummaryRecord:
    step_id: UUID
    block_index: int
    last_delta_seq: int
    text: str = field(repr=False)
    provider_run_ref: str
    status: Literal["streaming", "completed", "interrupted"]


@dataclass(frozen=True, slots=True)
class UserInterventionRecord:
    intervention_id: UUID
    loop_id: UUID
    message_id: UUID
    text: str = field(repr=False)
    status: Literal["pending", "consumed", "rejected"]
    consumed_by_step_id: UUID | None
    created_at: datetime
