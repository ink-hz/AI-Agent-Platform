from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.hr.standard_consent import StandardConsent
from app.execution_relay.contracts_v6 import HrTurnScope, HrMethodSelection
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
FeedbackReason = Literal[
    "inaccurate",
    "incomplete",
    "unclear",
    "unresolved",
    "file_format",
    "source_timeliness",
    "other",
]


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


def normalize_knowledge_selections(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("HR knowledge selection invalid")
    selected = []
    identities = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"source_commit", "id", "revision", "sha256"}:
            raise ValueError("HR knowledge selection invalid")
        for key, pattern in (("source_commit", r"[0-9a-f]{40}"), ("id", r"[a-z0-9][a-z0-9-]{0,127}"), ("sha256", r"[0-9a-f]{64}")):
            if not isinstance(item[key], str) or re.fullmatch(pattern, item[key]) is None:
                raise ValueError("HR knowledge selection invalid")
        if type(item["revision"]) is not int or item["revision"] < 1:
            raise ValueError("HR knowledge revision invalid")
        identity = (item["source_commit"], item["id"])
        if identity in identities:
            raise ValueError("HR knowledge selection duplicated")
        identities.add(identity)
        selected.append(dict(item))
    if len(json.dumps(selected, ensure_ascii=False).encode("utf-8")) > 8 * 1024:
        raise ValueError("HR knowledge selection too large")
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class ConversationTurnSubmission:
    text: str
    attachment_ids: tuple[UUID, ...] = ()
    active_attachment_ids: tuple[UUID, ...] = ()
    user_selected_resources: tuple[dict[str, object], ...] = ()
    hr_scope: HrTurnScope | None = None
    method_selection: HrMethodSelection | None = None
    standard_consent: StandardConsent | None = None
    input_result_refs: tuple[object, ...] | None = None
    trusted_channel_origin: dict[str,str] | None = field(default=None,repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_selected_resources", normalize_knowledge_selections(self.user_selected_resources))
        text = _normalized_text(self.text)
        attachment_ids = _normalized_ids(self.attachment_ids, maximum=5)
        active_attachment_ids = _normalized_ids(
            self.active_attachment_ids, maximum=50
        )
        if not set(attachment_ids).issubset(active_attachment_ids):
            raise ValueError("New attachments must be active")
        if self.hr_scope is not None:
            if not isinstance(self.hr_scope, HrTurnScope) or not set(active_attachment_ids).issubset(self.hr_scope.attachment_ids):
                raise ValueError("HR scope must include active attachments")
        if self.input_result_refs is not None:
            from app.execution_relay.contracts_v7 import HrResultRef
            if (len(self.input_result_refs)>20 or any(not isinstance(r,HrResultRef) for r in self.input_result_refs)
                or len({r.result_id for r in self.input_result_refs})!=len(self.input_result_refs)):
                raise ValueError("HR result inputs invalid")
        derived_method = None
        if self.user_selected_resources:
            derived_method = HrMethodSelection.model_validate_json(json.dumps({
                "resources": self.user_selected_resources,
                "catalogRelease": self.user_selected_resources[0]["source_commit"],
            }))
        if self.method_selection is not None and self.method_selection != derived_method:
            raise ValueError("Method selection must match selected knowledge resources")
        object.__setattr__(self, "method_selection", derived_method)
        if self.standard_consent is not None and (
            self.hr_scope is None or self.hr_scope.position_id is None
            or not self.standard_consent.accepts_text(self.text)
        ):
            raise ValueError("Standard confirmation text or position changed")
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
            value.text, value.attachment_ids, value.active_attachment_ids,
            user_selected_resources=value.user_selected_resources,
            hr_scope=value.hr_scope, method_selection=value.method_selection,
            standard_consent=value.standard_consent, input_result_refs=value.input_result_refs,
            trusted_channel_origin=value.trusted_channel_origin,
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


@dataclass(frozen=True, slots=True)
class ConversationCitationProjection:
    citation_key: str
    title: str
    url: str = field(repr=False)
    site: str
    retrieved_at: datetime
    supports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConversationArtifactVersionProjection:
    artifact_key: str
    version_no: int
    producer_version_id: str
    current: bool
    status: Literal["processing", "ready", "failed"]
    attachment: ConversationAttachmentProjection | None = None


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
    activity_status: str | None = None
    unread: bool = False
    execution_owner: str = "legacy_api_v1"


@dataclass(frozen=True, slots=True)
class ConversationReadStateRecord:
    conversation_id: UUID
    last_read_message_seq: int
    last_read_at: datetime


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
    citations: tuple[ConversationCitationProjection, ...] = ()
    artifact_versions: tuple[ConversationArtifactVersionProjection, ...] = ()
    result_delivery_status: Literal["pending", "completed", "failed"] | None = None
    user_selected_resources: tuple[dict[str, object], ...] = ()
    standard_consent: StandardConsent | None = None
    input_result_refs: tuple[object, ...] | None = None
    trusted_channel_origin: dict[str,str] | None = field(default=None,repr=False)


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
    triage_status: Literal["pending_triage", "triaged", "dismissed"] | None = None
    triaged_by_internal_user_id: UUID | None = None
    triaged_at: datetime | None = None


@dataclass(frozen=True)
class ConversationFeedbackReviewRecord:
    feedback: ConversationFeedbackRecord
    agent_id: str
    conversation_title: str
    question: str = field(repr=False)
    answer: str = field(repr=False)
    citations: tuple[ConversationCitationProjection, ...] = ()


@dataclass(frozen=True)
class ConversationReviewAttachmentRecord:
    attachment: ConversationAttachmentProjection
    artifact_key: str | None = None
    version_no: int | None = None
    current: bool = False


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
