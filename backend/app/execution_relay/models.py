from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RelayJobPayload(BaseModel):
    run_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    agent_id: str
    prompt: str
    max_turns: int = Field(ge=1, le=24)


class RelayEvent(BaseModel):
    run_id: UUID
    seq: int = Field(gt=0)
    event_type: str
    created_at: datetime
    payload: dict[str, object]


class RelayLease(BaseModel):
    job_id: UUID
    payload: RelayJobPayload
    lease_expires_at: datetime
    cancel_requested: bool
