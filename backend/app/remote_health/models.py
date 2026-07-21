from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


RemoteState = Literal["healthy", "degraded", "offline", "checking", "unknown"]


class RemoteAgentStatus(BaseModel):
    id: str
    name: str
    status: RemoteState = "checking"
    uptime_seconds: int | None = None
    checked_at: datetime | None = None
    error: str | None = None
    details: dict = Field(default_factory=dict)


class RemoteHealthSnapshot(BaseModel):
    healthy: bool
    checked_at: datetime | None = None
    error: str | None = None
    agents: list[RemoteAgentStatus] = Field(default_factory=list)


class RemoteOpsResult(BaseModel):
    admin_health: dict = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    admin_started_at: datetime | None = None
    fae_started_at: datetime | None = None

