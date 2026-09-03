from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.agent_brain.recovery import SearchRecoveryState
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
FeedbackReason = Literal["inaccurate", "incomplete", "unclear", "unresolved", "other"]


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Conversation text invalid")
    selected = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    try:
        if len(selected) > 32768:
            raise ValueError("Conversation text invalid")
    except UnicodeError:
        raise ValueError("Conversation text invalid") from None
    return selected


def _normalized_ids(value: object, *, maximum: int) -> tuple[UUID, ...]:
    if not isinstance(value, tuple) or any(not isinstance(item, UUID) for item in value):
        raise ValueError("Conversation attachment IDs invalid")
    if len(value) > maximum or len(set(value)) != len(value):
        raise ValueError("Conversation attachment IDs invalid")
    return tuple(sorted(value, key=str))


@dataclass(frozen=True, slots=True)
class ConversationTurnSubmission:
    text: str
    attachment_ids: tuple[UUID, ...] = ()
    active_attachment_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        text = _normalized_text(self.text)
        attachment_ids = _normalized_ids(self.attachment_ids, maximum=5)
        active_attachment_ids = _normalized_ids(
            self.active_attachment_ids, maximum=50
        )
        if not set(attachment_ids).issubset(active_attachment_ids):
            raise ValueError("New attachments must be active")
        if not text and not attachment_ids:
            raise ValueError("Conversation text or attachment required")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "attachment_ids", attachment_ids)
        object.__setattr__(self, "active_attachment_ids", active_attachment_ids)


def normalize_turn_submission(
    value: str | ConversationTurnSubmission,
) -> ConversationTurnSubmission:
    if isinstance(value, ConversationTurnSubmission):
        return ConversationTurnSubmission(
            value.text, value.attachment_ids, value.active_attachment_ids
        )
    if isinstance(value, str):
        return ConversationTurnSubmission(value)
    raise ValueError("Conversation submission invalid")


@dataclass(frozen=True, slots=True)
class ConversationAttachmentProjection:
    attachment_id: UUID
    conversation_id: UUID
    source: Literal["user", "agent"]
    display_name: str = field(repr=False)
    detected_mime: str | None
    size_bytes: int
    state: str
    created_at: datetime
    retained_until: datetime
    processing_coverage: dict[str, object] | None
    availability_reason: str | None


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
    input_attachments: tuple[ConversationAttachmentProjection, ...] = ()
    output_attachments: tuple[ConversationAttachmentProjection, ...] = ()
    active_attachment_ids: tuple[UUID, ...] = ()
    search_recovery: SearchRecoveryState | None = None


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
class ConversationInterventionResult:
    message: ConversationMessageRecord
    turn: ConversationTurnRecord
    status: Literal["pending", "consumed"]
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
    reason: FeedbackReason | None
    created_at: datetime
    comment: str | None = field(default=None, repr=False)


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
