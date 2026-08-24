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
    "accepted",
    "running",
    "waiting_agents",
    "waiting_user",
    "completing",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]
FeedbackRating = Literal["helpful", "unhelpful"]


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
    retry_of_turn_id: UUID | None = None


@dataclass(frozen=True)
class ConversationEventRecord:
    event_id: UUID
    conversation_id: UUID
    seq: int
    turn_id: UUID | None
    mission_id: UUID | None
    event_type: str
    created_at: datetime
    payload: dict[str, object] = field(repr=False)


@dataclass(frozen=True)
class ConversationCreateResult:
    conversation: ConversationRecord
    message: ConversationMessageRecord
    turn: ConversationTurnRecord
    mission: MissionRecord | None
    created: bool


@dataclass(frozen=True)
class ConversationFeedbackRecord:
    feedback_id: UUID
    owner_internal_user_id: UUID
    conversation_id: UUID
    message_id: UUID
    turn_id: UUID
    mission_id: UUID | None
    rating: FeedbackRating
    created_at: datetime


@dataclass(frozen=True)
class ConversationFeedbackResult:
    feedback: ConversationFeedbackRecord
    created: bool


@dataclass(frozen=True)
class ConversationMetrics:
    conversations: int
    multi_turn_conversations: int
    multi_turn_rate: float
    turns: int
    completed_turns: int
    turn_completion_rate: float
    missions: int
    rated_missions: int
    helpful_missions: int
    mission_quality_rate: float | None
