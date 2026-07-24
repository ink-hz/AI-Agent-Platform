from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ReadinessStatus = Literal["Ready", "Busy", "Limited", "Offline", "Unknown"]
RuntimeFreshness = Literal["live", "stale", "unavailable"]
ModelSource = Literal["runtime", "trace", "configured", "unavailable"]
ChannelStatus = Literal[
    "connected", "connecting", "reconnecting", "failed", "unknown"
]


class Readiness(BaseModel):
    status: ReadinessStatus
    reason: str
    observed_at: datetime | None = None
    freshness: RuntimeFreshness = "unavailable"


class AgentRuntime(BaseModel):
    engine: str | None = None
    model: str
    model_source: ModelSource
    backend: str | None = None
    channel: str | None = None
    channel_status: ChannelStatus = "unknown"
    active_turns: int | None = None
    process_uptime_seconds: int | None = None


class AgentLifecycle(BaseModel):
    live_since: datetime | None = None
    last_updated_at: datetime | None = None
    production_runtime_seconds: int | None = None


class RuntimeEvidence(BaseModel):
    kind: str
    source: str
    status: str
    observed_at: datetime | None = None
    summary: str


class AgentRuntimeView(BaseModel):
    agent_id: str
    readiness: Readiness
    runtime: AgentRuntime
    lifecycle: AgentLifecycle
    evidence: list[RuntimeEvidence] = Field(default_factory=list)
