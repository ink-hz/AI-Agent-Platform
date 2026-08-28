from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID

import jcs
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

CONTRACT_VERSION = "orbbec-http-task/v1"
TERMINAL_EVENT_KINDS = frozenset({"result", "failed", "timeout", "cancelled"})
CANONICAL_EVENT_KINDS = frozenset(
    {
        "thinking_summary",
        "message",
        "work_update",
        "artifact",
        "input_required",
        "action_required",
        "finding",
        *TERMINAL_EVENT_KINDS,
    }
)
ContractVersion = Literal["orbbec-http-task/v1"]
TaskStatus = Literal[
    "queued",
    "running",
    "waiting_input",
    "waiting_confirmation",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
]
TerminalTaskStatus = Literal["completed", "failed", "cancelled", "timed_out"]
EventKind = Literal[
    "thinking_summary",
    "message",
    "work_update",
    "artifact",
    "input_required",
    "action_required",
    "finding",
    "result",
    "failed",
    "timeout",
    "cancelled",
]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be an RFC 3339 UTC value")
    return value


def _require_sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        raise ValueError("list must not be empty")
    if tuple(sorted(set(values))) != values:
        raise ValueError("list must be unique and sorted")
    return values


class CapabilitiesResponse(StrictContractModel):
    contract_version: ContractVersion
    agent_id: str = Field(min_length=1)
    capability_version: PositiveInt
    supports_actions: bool
    max_duration_seconds: int = Field(gt=0, le=900)
    supported_scopes: tuple[str, ...]
    supported_event_kinds: tuple[EventKind, ...]

    _sorted_scopes = field_validator("supported_scopes")(_require_sorted_unique)

    @field_validator("supported_event_kinds")
    @classmethod
    def _event_kinds_are_sorted_unique(
        cls, values: tuple[EventKind, ...]
    ) -> tuple[EventKind, ...]:
        checked = _require_sorted_unique(tuple(values))
        return tuple(checked)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _actions_are_declared(self) -> CapabilitiesResponse:
        if self.supports_actions and "action_required" not in self.supported_event_kinds:
            raise ValueError("supports_actions requires action_required")
        return self


class HealthResponse(StrictContractModel):
    contract_version: ContractVersion
    status: Literal["healthy", "degraded", "unavailable"]
    capability_version: PositiveInt


class TaskResponse(StrictContractModel):
    contract_version: ContractVersion
    downstream_task_id: str = Field(min_length=1)
    platform_task_id: UUID
    status: TaskStatus
    cancel_requested: bool
    next_event_seq: PositiveInt
    terminal: bool
    created_at: datetime
    updated_at: datetime

    _created_at_is_utc = field_validator("created_at")(_require_utc)
    _updated_at_is_utc = field_validator("updated_at")(_require_utc)

    @model_validator(mode="after")
    def _terminal_matches_status(self) -> TaskResponse:
        expected = self.status in {"completed", "failed", "cancelled", "timed_out"}
        if self.terminal is not expected:
            raise ValueError("terminal must match task status")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class CreateTaskRequest(StrictContractModel):
    contract_version: ContractVersion
    platform_task_id: UUID
    conversation_ref: str = Field(min_length=1)
    turn_ref: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    context_excerpt: tuple[str, ...]
    constraints: tuple[str, ...]
    attachment_refs: tuple[UUID, ...]
    expected_output: str = Field(min_length=1)
    capability_version: PositiveInt
    idempotency_key: str = Field(min_length=1)
    deadline_at: datetime
    authorized_scopes: tuple[str, ...]

    _deadline_is_utc = field_validator("deadline_at")(_require_utc)
    _sorted_authorized_scopes = field_validator("authorized_scopes")(
        _require_sorted_unique
    )


class CreateTaskReceipt(StrictContractModel):
    contract_version: ContractVersion
    downstream_task_id: str = Field(min_length=1)
    status: Literal["queued"]
    next_event_seq: Literal[1]
    duplicate: bool


class MessageRequest(StrictContractModel):
    contract_version: ContractVersion
    message_seq: PositiveInt
    content: str = Field(min_length=1)
    attachment_refs: tuple[UUID, ...]
    idempotency_key: str = Field(min_length=1)


class MessageReceipt(StrictContractModel):
    contract_version: ContractVersion
    downstream_task_id: str = Field(min_length=1)
    message_seq: PositiveInt
    status: Literal["accepted"]
    duplicate: bool


class CancelRequest(StrictContractModel):
    contract_version: ContractVersion
    idempotency_key: str = Field(min_length=1)


class CancelReceipt(StrictContractModel):
    contract_version: ContractVersion
    downstream_task_id: str = Field(min_length=1)
    cancel_request_id: str = Field(min_length=1)
    status: Literal[
        "cancel_requested", "cancelled", "completed", "failed", "timed_out"
    ]
    duplicate: bool


class ActionExecuteRequest(StrictContractModel):
    contract_version: ContractVersion
    action_id: UUID
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1)


class ActionExecuteReceipt(StrictContractModel):
    contract_version: ContractVersion
    action_id: UUID
    execution_id: str = Field(min_length=1)
    status: Literal["queued", "running", "completed", "failed"]
    duplicate: bool


class ErrorDetails(StrictContractModel):
    current_capability_version: PositiveInt | None = None
    must_refresh_capabilities: Literal[True] | None = None
    expected_sequence: NonNegativeInt | None = None
    supported_contract_versions: tuple[str, ...] | None = None


class ContractError(StrictContractModel):
    code: Literal[
        "contract_version_unsupported",
        "protocol_violation",
        "idempotency_conflict",
        "scope_denied",
        "task_not_found",
        "task_terminal",
        "message_sequence_conflict",
        "event_sequence_conflict",
        "action_conflict",
        "action_digest_mismatch",
        "action_expired",
        "capability_changed",
        "deadline_expired",
        "attachment_unsupported",
        "upstream_unavailable",
    ]
    message: str = Field(min_length=1)
    details: ErrorDetails


class ErrorEnvelope(StrictContractModel):
    contract_version: ContractVersion
    error: ContractError


class ActionDigestInput(StrictContractModel):

    platform_task_id: UUID
    action_seq: PositiveInt
    action_kind: str
    parameters: dict[str, object]


class ActionProposal(StrictContractModel):

    action_id: UUID
    action_seq: PositiveInt
    action_kind: str
    summary: str
    impact: str
    parameters: dict[str, object]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    execution_timeout_seconds: int = Field(gt=0, le=900)

    _expires_at_is_utc = field_validator("expires_at")(_require_utc)


class TaskEvent(StrictContractModel):

    seq: PositiveInt
    kind: EventKind
    created_at: datetime
    payload: dict[str, object]

    _created_at_is_utc = field_validator("created_at")(_require_utc)


class EventPage(StrictContractModel):

    contract_version: ContractVersion
    downstream_task_id: str = Field(min_length=1)
    events: tuple[TaskEvent, ...]
    next_after: int = Field(ge=0)
    terminal: bool


def _digest_input(value: ActionDigestInput | Mapping[str, object]) -> ActionDigestInput:
    if isinstance(value, ActionDigestInput):
        return value
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("action digest input must be valid JSON") from exc
    return ActionDigestInput.model_validate_json(encoded)


def canonical_action_bytes(
    value: ActionDigestInput | Mapping[str, object],
) -> bytes:
    validated = _digest_input(value)
    document = {
        "platform_task_id": str(validated.platform_task_id),
        "action_seq": validated.action_seq,
        "action_kind": validated.action_kind,
        "parameters": validated.parameters,
    }
    try:
        return jcs.canonicalize(document)
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(
            "action digest input cannot be canonicalized as RFC 8785 JSON"
        ) from exc


def action_digest(value: ActionDigestInput | Mapping[str, object]) -> str:
    return sha256(canonical_action_bytes(value)).hexdigest()
