from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


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
