from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


RelayJobKind = Literal["legacy_brain", "direct_agent", "metabot_local"]
RelayResultMode = Literal["internal", "public_markdown"]


class RequesterSubject(BaseModel):
    """Minimal Platform-verified identity carried outside the user prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    internal_user_id: UUID
    display_name: str = Field(min_length=1, max_length=256, strict=True)

    @field_validator("display_name")
    @classmethod
    def _valid_display_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("requester display name invalid")
        return value


class RelayJobPayload(BaseModel):
    run_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    agent_id: str
    prompt: str
    max_turns: int = Field(ge=1, le=24)
    job_kind: RelayJobKind = "legacy_brain"
    result_mode: RelayResultMode = "internal"
    requester_subject: RequesterSubject | None = Field(default=None, repr=False)


class RelayEvent(BaseModel):
    run_id: UUID
    seq: int = Field(gt=0)
    event_type: str
    created_at: AwareDatetime
    payload: dict[str, object]


class RelayLease(BaseModel):
    job_id: UUID
    payload: RelayJobPayload
    lease_expires_at: datetime
    cancel_requested: bool
