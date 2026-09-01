from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RawAttachment:
    attachment_id: str
    direction: str
    display_name: str
    mime_type: str | None
    size_bytes: int | None
    received_or_generated_at: datetime
    archive_status: str | None
    delivery_status: str | None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RawTurn:
    turn_key: str
    turn_index: int
    question: str | None
    answer: str | None
    created_at: datetime
    question_at: datetime | None = None
    answer_at: datetime | None = None
    question_time_status: str | None = None
    answer_time_status: str | None = None
    outcome: str | None = None
    fallback_used: bool | None = None
    duration_ms: int | None = None
    feedback_sentiments: tuple[str, ...] = ()
    review_statuses: tuple[str, ...] = ()
    trace: RawTraceAggregate | None = None
    attachments: tuple[RawAttachment, ...] = ()
    sources: tuple[dict[str, Any], ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawTraceAggregate:
    status: str | None
    duration_ms: int | None
    engine: str | None
    backend: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    error_class: str | None
    tool_categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RawSession:
    session_key: str
    agent_id: str
    source_kind: str
    channel: str | None
    title: str | None
    user_identity: str | None
    primary_sender_name: str | None
    primary_sender_department: str | None
    created_at: datetime
    last_active_at: datetime
    replica_updated_at: datetime | None = None
    turns: tuple[RawTurn, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def replication_cursor_at(self) -> datetime:
        return self.replica_updated_at or self.last_active_at


@dataclass(frozen=True, slots=True)
class SanitizedText:
    text: str
    safe: bool
    sha256: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class SanitizedAttachment:
    display_label: str
    category: str
    mime_type: str | None
    size_bucket: str
    direction: str
    archive_status: str | None
    delivery_status: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SanitizedTurnRecord:
    turn_index: int
    question: SanitizedText
    answer: SanitizedText
    created_at: datetime
    outcome: str | None
    fallback_used: bool | None
    duration_ms: int | None
    attachments: tuple[SanitizedAttachment, ...]
    trace: SanitizedTraceAggregate | None = None
    question_at: datetime | None = None
    answer_at: datetime | None = None
    question_time_status: str = "unavailable"
    answer_time_status: str = "unavailable"
    feedback_sentiments: tuple[str, ...] = ()
    review_statuses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SanitizedTraceAggregate:
    status: str | None
    duration_ms: int | None
    engine: str | None
    backend: str | None
    model_family: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    error_class: str | None
    tool_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SanitizedSessionRecord:
    agent_id: str
    source_kind: str
    channel: str | None
    title: SanitizedText
    primary_sender_name: str | None
    primary_sender_department: str | None
    created_at: datetime
    last_active_at: datetime
    turns: tuple[SanitizedTurnRecord, ...]
    sanitizer_policy_version: str


@dataclass(frozen=True, slots=True)
class ReviewIssueProjection:
    issue_id: UUID
    agent_id: str
    status: str
    priority: str
    title: str
    failure_layer: str | None
    owner_display: str | None
    linked_turn_count: int
    linked_turn_keys: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    scope_valid: bool = False
    detail_schema_version: int | None = None
    origin_turn_key: str | None = None
    root_cause: str | None = None
    impact_scope: str | None = None
    secondary_layers: tuple[str, ...] = ()
    links: tuple[dict[str, Any], ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    replays: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    progress: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReviewInboxProjection:
    agent_id: str
    turn_key: str
    feedback_count: int
    first_feedback_at: datetime
    scope_valid: bool = False


@dataclass(frozen=True, slots=True)
class ReviewFeedbackTotalsProjection:
    """Per-Agent feedback totals.

    The Review inbox only carries negative feedback that nobody has triaged
    yet, so it cannot answer "how much feedback exists". Counting it as the
    total makes a fully triaged Agent look like an Agent nobody reviewed, and
    it can never represent positive feedback at all.
    """

    agent_id: str
    feedback_rows: int
    negative_rows: int
    negative_turns: int
    positive_rows: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class OperationEventProjection:
    event_id: str
    agent_id: str | None
    event_type: str
    event_family: str
    severity: str
    status: str
    title: str
    summary: str
    source_kind: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class FaeReportProjection:
    projection_kind: str
    report_id: str
    report_version: int
    item_id: str
    occurred_at: datetime
    payload: dict[str, Any]
    agent_id: str = "ai-fae-agent"
