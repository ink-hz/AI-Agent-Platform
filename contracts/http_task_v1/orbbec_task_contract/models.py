from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

import jcs
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

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


class ActionDigestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    platform_task_id: UUID
    action_seq: PositiveInt
    action_kind: str
    parameters: dict[str, object]


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action_id: UUID
    action_seq: PositiveInt
    action_kind: str
    summary: str
    impact: str
    parameters: dict[str, object]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    execution_timeout_seconds: int = Field(gt=0, le=900)


class TaskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    seq: PositiveInt
    kind: Literal[
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
    created_at: datetime
    payload: dict[str, object]


class EventPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

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
