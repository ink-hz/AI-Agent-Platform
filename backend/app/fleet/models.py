from typing import Literal

from pydantic import BaseModel, Field


FleetState = Literal["active", "online", "degraded", "offline", "checking", "unknown"]
AgentVisibility = Literal["business", "system"]
LifecycleBasis = Literal[
    "release_artifact",
    "repository_history",
    "earliest_session",
    "not_recorded",
]


class TrendPoint(BaseModel):
    date: str
    conversations: int


class FleetAgent(BaseModel):
    id: str
    name: str
    domain: str
    description: str
    glyph: str
    accent: str
    visibility: AgentVisibility
    state: FleetState
    live_since: str | None
    live_since_basis: LifecycleBasis
    last_updated_at: str | None
    last_updated_basis: LifecycleBasis
    current_runtime_seconds: int | None
    total_conversations: int | None
    conversations_last_7d: int | None
    last_activity_at: str | None
    recent_summary: str | None
    session_count: int | None = None
    last_synced_at: str | None = None
    data_freshness: Literal["live", "fresh", "stale", "unavailable"] = "live"


class FleetSummary(BaseModel):
    total_agents: int
    running_agents: int
    active_agents: int
    degraded_agents: int
    offline_agents: int
    checking_agents: int
    total_conversations: int | None
    conversations_last_7d: int | None
    conversations_previous_7d: int | None
    change_percent: float | None


class DataSourceStatus(BaseModel):
    healthy: bool
    checked_at: str | None
    stale: bool = False
    error: str | None = None


class FleetOverview(BaseModel):
    summary: FleetSummary
    trend: list[TrendPoint]
    agents: list[FleetAgent]
    expected_agent_ids: list[str] = Field(default_factory=list, exclude=True)
    runtime_source: DataSourceStatus
    usage_source: DataSourceStatus
