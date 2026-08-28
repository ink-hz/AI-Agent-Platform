from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

import jcs
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionDigestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    platform_task_id: UUID
    action_seq: int = Field(gt=0)
    action_kind: str = Field(min_length=1, max_length=128)
    parameters: dict[str, object]


def canonical_action_bytes(
    value: ActionDigestInput | Mapping[str, object],
) -> bytes:
    if not isinstance(value, ActionDigestInput):
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            value = ActionDigestInput.model_validate_json(encoded)
        except (TypeError, ValueError, UnicodeError) as error:
            raise ValueError("action digest input invalid") from error
    document = {
        "platform_task_id": str(value.platform_task_id),
        "action_seq": value.action_seq,
        "action_kind": value.action_kind,
        "parameters": value.parameters,
    }
    try:
        return jcs.canonicalize(document)
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("action digest input invalid") from error


def stable_action_id(platform_task_id: UUID, action_seq: int) -> UUID:
    if not isinstance(platform_task_id, UUID) or type(action_seq) is not int:
        raise ValueError("action identity invalid")
    if action_seq <= 0:
        raise ValueError("action identity invalid")
    return uuid5(platform_task_id, f"action:{action_seq}")


def proposal_digest(
    *,
    platform_task_id: UUID,
    action_seq: int,
    action_kind: str,
    parameters: dict[str, object],
) -> str:
    canonical = canonical_action_bytes(
        ActionDigestInput(
            platform_task_id=platform_task_id,
            action_seq=action_seq,
            action_kind=action_kind,
            parameters=parameters,
        )
    )
    return hashlib.sha256(canonical).hexdigest()


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_id: UUID
    platform_task_id: UUID
    action_seq: int = Field(gt=0)
    action_kind: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=8192)
    impact: str = Field(min_length=1, max_length=8192)
    parameters: dict[str, object]
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    execution_timeout_seconds: int = Field(gt=0, le=900)

    @model_validator(mode="after")
    def _matches_canonical_identity_and_digest(self) -> ActionProposal:
        if self.action_id != stable_action_id(
            self.platform_task_id, self.action_seq
        ):
            raise ValueError("action identity mismatch")
        expected = proposal_digest(
            platform_task_id=self.platform_task_id,
            action_seq=self.action_seq,
            action_kind=self.action_kind,
            parameters=self.parameters,
        )
        if self.action_digest != expected:
            raise ValueError("action digest mismatch")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("action expiry invalid")
        return self


class ActionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_id: UUID
    task_id: UUID
    action_seq: int
    action_kind: str
    summary: str
    impact: str
    action_digest: str
    status: Literal["pending", "confirmed", "rejected", "expired", "superseded"]
    expires_at: datetime
    execution_status: Literal[
        "not_started", "queued", "running", "completed", "failed"
    ]
    confirmed_by_internal_user_id: UUID | None = None
    confirmed_at: datetime | None = None
    execution_deadline_at: datetime | None = None
