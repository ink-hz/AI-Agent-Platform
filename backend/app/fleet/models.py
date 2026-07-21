from typing import Literal

from pydantic import BaseModel


FleetState = Literal["active", "online", "degraded", "offline", "checking", "unknown"]


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
    state: FleetState
    uptime_seconds: int | None
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
    runtime_source: DataSourceStatus
    usage_source: DataSourceStatus
