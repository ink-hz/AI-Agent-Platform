from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


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
    collaboration_contract: Literal["core_chat_collaboration_v3"] | None = None
    task_session_id: str | None = Field(default=None, min_length=16, max_length=256)
    message_kind: Literal["initial", "followup", "stop"] = "initial"
    message_seq: int = Field(default=1, ge=1)
    parent_run_id: UUID | None = None

    @model_validator(mode="after")
    def _valid_collaboration_command(self) -> "RelayJobPayload":
        collaboration_values = (
            self.collaboration_contract,
            self.task_session_id,
            self.parent_run_id,
        )
        if self.job_kind != "metabot_local":
            if (
                any(value is not None for value in collaboration_values)
                or self.message_kind != "initial"
                or self.message_seq != 1
            ):
                raise ValueError("collaboration command requires metabot_local")
            return self
        if (
            self.agent_id == "agent-brain-bot"
            or self.collaboration_contract != "core_chat_collaboration_v3"
            or self.task_session_id is None
        ):
            raise ValueError("metabot_local collaboration command invalid")
        if self.message_kind == "initial":
            if self.message_seq != 1 or self.parent_run_id is not None:
                raise ValueError("initial collaboration command invalid")
        elif self.message_kind == "followup":
            if self.message_seq <= 1 or self.parent_run_id is None:
                raise ValueError("follow-up collaboration command invalid")
        elif self.parent_run_id is None:
            raise ValueError("stop collaboration command invalid")
        return self


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
