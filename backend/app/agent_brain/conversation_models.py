from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.agent_brain.repository import MissionRecord


ConversationMode = Literal["brain", "direct_agent"]
ConversationStatus = Literal["active", "archived"]
MessageRole = Literal["user", "assistant", "system"]
MessageDeliveryStatus = Literal["accepted", "streaming", "completed", "failed"]
TurnStatus = Literal[
    "accepted", "running", "completed", "failed", "cancelled", "interrupted"
]


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: UUID
    owner_internal_user_id: UUID
    started_by_client_request_id: UUID
    mode: ConversationMode
    direct_agent_id: str | None
    title: str
    status: ConversationStatus
    summary_through_seq: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    summary: str | None = field(repr=False)
    summary_key_version: int | None = field(repr=False)


@dataclass(frozen=True)
class ConversationMessageRecord:
    message_id: UUID
    conversation_id: UUID
    seq: int
    role: MessageRole
    turn_id: UUID | None
    mission_id: UUID | None
    delivery_status: MessageDeliveryStatus
    created_at: datetime
    completed_at: datetime | None
    content: str = field(repr=False)


@dataclass(frozen=True)
class ConversationTurnRecord:
    turn_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    client_request_id: UUID
    mission_id: UUID | None
    status: TurnStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConversationCreateResult:
    conversation: ConversationRecord
    message: ConversationMessageRecord
    turn: ConversationTurnRecord
    mission: MissionRecord
    created: bool
