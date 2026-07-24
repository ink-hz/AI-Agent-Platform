from typing import Literal

from pydantic import BaseModel, Field


InstanceState = Literal["healthy", "degraded", "offline", "checking"]
ChannelState = Literal[
    "connected", "connecting", "reconnecting", "failed", "unknown"
]


class MonitorTarget(BaseModel):
    id: str
    name: str
    pm2_name: str
    port: int = Field(ge=1, le=65535)
    health_url: str
    runtime_url: str
    workdir: str = ""
    engine: str | None = None
    declared_model: str | None = None
    backend: str | None = None
    channel: str | None = None


class InstanceStatus(BaseModel):
    id: str
    name: str
    pm2_name: str
    port: int
    status: InstanceState = "checking"
    uptime_seconds: int | None = None
    latency_ms: int | None = None
    checked_at: str | None = None
    error: str | None = None
    engine: str | None = None
    declared_model: str | None = None
    observed_model: str | None = None
    backend: str | None = None
    channel: str | None = None
    channel_status: ChannelState = "unknown"
    active_turns: int | None = None
    runtime_observed_at: str | None = None


class ClusterSummary(BaseModel):
    total: int
    healthy: int
    degraded: int
    offline: int
    checking: int


class SourceStatus(BaseModel):
    healthy: bool
    checked_at: str | None = None
    error: str | None = None


class ClusterSnapshot(BaseModel):
    summary: ClusterSummary
    source: SourceStatus
    instances: list[InstanceStatus]
