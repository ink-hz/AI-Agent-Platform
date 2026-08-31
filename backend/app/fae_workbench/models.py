from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field


FAE_AGENT_ID = "ai-fae-agent"
FAE_SOURCE_KIND = "fae"
FAE_SOURCE_ENVIRONMENT = "production"


class FaeTrendPoint(BaseModel):
    day: date
    sessions: int = Field(ge=0)
    negative_turns: int = Field(ge=0)


class FaeSessionAttention(BaseModel):
    session_key: str
    title: str | None = None
    last_active_at: datetime
    reason: Literal["fallback", "failed_outcome", "empty_answer"]


class FaeOperationalSnapshot(BaseModel):
    period_start: datetime
    period_end: datetime
    data_as_of: datetime | None
    session_count: int = Field(ge=0)
    active_subject_count: int = Field(ge=0)
    negative_feedback_events: int = Field(ge=0)
    negative_turn_count: int = Field(ge=0)
    abnormal_session_count: int = Field(ge=0)
    p50_duration_ms: int | None
    p95_duration_ms: int | None
    trend: list[FaeTrendPoint] = Field(default_factory=list)
    attention: list[FaeSessionAttention] = Field(default_factory=list)


class FaeFeedbackProjection(BaseModel):
    """A complete period-bounded cloud feedback projection for FAE."""

    period_start: datetime
    period_end: datetime
    negative_feedback_events: int = Field(ge=0)
    negative_turn_count: int = Field(ge=0)
    daily_negative_turns: dict[date, int] = Field(default_factory=dict)


SectionStatus = Literal["available", "unavailable"]
FaeFreshnessStatus = Literal["fresh", "stale", "unavailable"]


class FaeSectionState(BaseModel):
    status: SectionStatus
    as_of: AwareDatetime | None = None
    error_code: str | None = None


class FaeFreshness(BaseModel):
    status: FaeFreshnessStatus
    data_as_of: AwareDatetime | None = None


class FaeSummary(BaseModel):
    session_count: int = Field(ge=0)
    active_subject_count: int = Field(ge=0)
    negative_feedback_events: int = Field(ge=0)
    negative_turn_count: int = Field(ge=0)
    abnormal_session_count: int = Field(ge=0)
    open_issue_count: int | None = Field(default=None, ge=0)
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None


class FaeSummarySection(BaseModel):
    state: FaeSectionState
    data: FaeSummary | None = None


class FaeAttentionSection(BaseModel):
    state: FaeSectionState
    items: list[FaeSessionAttention] = Field(default_factory=list)


class FaeTrendSection(BaseModel):
    state: FaeSectionState
    points: list[FaeTrendPoint] = Field(default_factory=list)


class FaeIssueSection(BaseModel):
    state: FaeSectionState
    statuses: dict[str, int] = Field(default_factory=dict)


class FaeReportPreviewSection(BaseModel):
    state: FaeSectionState


class FaeOverview(BaseModel):
    period_start: AwareDatetime
    period_end: AwareDatetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    freshness: FaeFreshness
    summary: FaeSummarySection
    attention: FaeAttentionSection
    trends: FaeTrendSection
    issues: FaeIssueSection
    reports: FaeReportPreviewSection
