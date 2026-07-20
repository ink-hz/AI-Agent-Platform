from typing import Literal

from pydantic import BaseModel, Field


InstanceState = Literal["healthy", "degraded", "offline", "checking"]


class MonitorTarget(BaseModel):
    id: str
    name: str
    pm2_name: str
    port: int = Field(ge=1, le=65535)
    health_url: str
    workdir: str = ""


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
