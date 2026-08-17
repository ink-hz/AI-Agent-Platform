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
    outcome: str | None = None
    fallback_used: bool | None = None
    duration_ms: int | None = None
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
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewInboxProjection:
    agent_id: str
    turn_key: str
    feedback_count: int
    first_feedback_at: datetime


@dataclass(frozen=True, slots=True)
class OperationEventProjection:
    event_id: str
    agent_id: str
    event_type: str
    severity: str
    summary: str
    occurred_at: datetime
