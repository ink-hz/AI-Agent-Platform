from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field


SourceKind = Literal["metabot", "fae", "admin"]
AgentVisibility = Literal["business", "system"]
Availability = Literal["available", "missing", "unavailable", "restricted"]
Freshness = Literal["live", "fresh", "stale"]


class SessionFilters(BaseModel):
    agent_id: str | None = None
    source_kind: SourceKind | None = None
    channel: str | None = None
    query: str | None = None
    sentiment: Literal["positive", "negative", "other"] | None = None
    review_status: str | None = None
    outcome: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class FlywheelFilters(BaseModel):
    agent_id: str | None = None
    item_type: Literal["evaluation", "knowledge", "qa"] | None = None
    status: str | None = None


class AgentSummary(BaseModel):
    id: str
    name: str
    domain: str
    description: str
    glyph: str
    accent: str
    visibility: AgentVisibility
    source_kind: SourceKind
    deployment: str
    session_count: int
    total_turns: int
    last_activity_at: datetime | None = None
    last_synced_at: datetime | None = None
    freshness: Freshness


class SessionSummary(BaseModel):
    session_key: str
    agent_id: str
    source_kind: SourceKind
    channel: str
    title: str | None = None
    created_at: datetime
    last_active_at: datetime
    turn_count: int
    feedback_count: int
    review_count: int
    latest_outcome: str | None = None
    source_synced_at: datetime | None = None
    freshness: Freshness


class EvidenceSummary(BaseModel):
    kind: str
    title: str
    reference: str | None = None
    classification: str | None = None
    availability: Availability = "available"
    metadata: dict = Field(default_factory=dict)


class FeedbackItem(BaseModel):
    feedback_key: str
    sentiment: Literal["positive", "negative", "other"]
    raw_rating: str
    reason_code: str | None = None
    comment: str = ""
    created_at: datetime
    details: dict = Field(default_factory=dict)


class ReviewItem(BaseModel):
    review_key: str
    status: str
    native_priority: str
    normalized_priority: str
    failure_layer: str | None = None
    notes: str = ""
    corrected_answer: str = ""
    reviewer: str
    created_at: datetime
    updated_at: datetime
    details: dict = Field(default_factory=dict)


class ImprovementItem(BaseModel):
    item_key: str
    turn_key: str | None = None
    agent_id: str
    source_kind: SourceKind
    item_type: Literal["evaluation", "knowledge", "qa"]
    status: str
    priority: str | None = None
    title: str
    summary: str
    created_at: datetime
    updated_at: datetime
    source_synced_at: datetime | None = None
    details: dict = Field(default_factory=dict)


class TurnDetail(BaseModel):
    turn_key: str
    session_key: str
    agent_id: str
    source_kind: SourceKind
    turn_index: int
    question: str
    answer: str
    created_at: datetime
    trace_key: str | None = None
    outcome: str | None = None
    fallback_used: bool = False
    duration_ms: int | None = None
    sources: list[dict] = Field(default_factory=list)
    evidence: list[EvidenceSummary] = Field(default_factory=list)
    evidence_availability: Availability = "available"
    feedback: list[FeedbackItem] = Field(default_factory=list)
    reviews: list[ReviewItem] = Field(default_factory=list)
    improvements: list[ImprovementItem] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)


class SessionDetail(SessionSummary):
    turns: list[TurnDetail] = Field(default_factory=list)


class TraceStep(BaseModel):
    step_key: str
    trace_key: str
    kind: Literal["stage", "span", "tool_call", "event"]
    name: str
    status: str | None = None
    parent_step_key: str | None = None
    seq: int | None = None
    started_at: datetime | None = None
    duration_ms: int | None = None
    input_summary: dict = Field(default_factory=dict)
    output_summary: dict = Field(default_factory=dict)
    safe_metadata: dict = Field(default_factory=dict)
    error_summary: str | None = None


class TraceDetail(BaseModel):
    trace_key: str
    turn_key: str
    agent_id: str
    source_kind: SourceKind
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    engine: str | None = None
    backend: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    error_class: str | None = None
    error_message: str | None = None
    detail_availability: Availability
    source_synced_at: datetime | None = None
    details: dict = Field(default_factory=dict)
    steps: list[TraceStep] = Field(default_factory=list)


class FlywheelOverview(BaseModel):
    feedback_total: int
    negative_feedback: int
    pending_reviews: int
    evaluation_candidates: int
    knowledge_tasks: int
    qa_candidates: int


class SyncStatus(BaseModel):
    source_kind: Literal["fae", "admin"]
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    completed_at: datetime | None = None
    source_counts: dict = Field(default_factory=dict)
    applied_counts: dict = Field(default_factory=dict)
    validation: dict = Field(default_factory=dict)
    error_summary: str | None = None
    freshness: Freshness


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
