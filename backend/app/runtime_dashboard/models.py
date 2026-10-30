"""Content-free, versioned dashboard HTTP contract."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Metric(Model):
    value: int | None = Field(default=None, ge=0)
    status: Literal['observed', 'partial', 'unavailable', 'not_applicable'] = 'unavailable'
    unit: Literal['session', 'turn', 'delivery', 'work', 'result', 'document', 'project', 'task', 'application', 'agent']
    definition: str
    reason: str | None = None


class Counts(Model):
    sessions: Metric
    turns: Metric
    answered_turns: Metric
    deliveries: Metric


class Usage(Model):
    retained: Counts
    window: Counts
    last_activity_at: AwareDatetime | None = None
    outcome_counts: dict[str, int] = Field(default_factory=dict)


class SourceEvidence(Model):
    source_id: str
    source_kind: str
    channel: str | None = None
    status: Literal['fresh', 'stale', 'unavailable', 'not_applicable'] = 'unavailable'
    observed_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    checked_at: AwareDatetime | None = None
    complete: bool = False
    error: str | None = None
    channels: list[str | None] = Field(default_factory=list)
    permission_scope: str = 'platform_management'
    reader_role: str | None = None


class RuntimeEvidence(Model):
    coverage: Literal['observed', 'not_configured', 'not_observed', 'not_applicable'] = 'not_observed'
    state: Literal['healthy', 'degraded', 'offline', 'unknown'] = 'unknown'
    observed_at: AwareDatetime | None = None
    checked_at: AwareDatetime | None = None
    stale: bool = False
    method: str | None = None
    error: str | None = None


class Deployment(Model):
    version: str | None = None
    observed_at: AwareDatetime | None = None
    evidence: str | None = None


class HealthObservation(Model):
    runtime: RuntimeEvidence = Field(default_factory=RuntimeEvidence)
    deployment: Deployment = Field(default_factory=Deployment)


class Application(Model):
    id: str
    name: str
    kind: Literal['agent', 'workspace']
    entry_path: str | None = None
    can_use: bool = False
    runtime: RuntimeEvidence = Field(default_factory=RuntimeEvidence)
    source: SourceEvidence
    usage: Usage
    business_metrics: dict[str, Metric] = Field(default_factory=dict)
    business_sources: dict[str, SourceEvidence] = Field(default_factory=dict)
    deployment: Deployment = Field(default_factory=Deployment)
    dataset: dict[str, str] | None = None


class Channel(Model):
    id: str
    agent_id: str
    name: str
    source: SourceEvidence
    usage: Usage
    configured: bool = True
    records_path: str


class Inventory(Model):
    applications: int
    agent_services: int
    business_workspaces: int
    configured_agents: int
    channel_agents: int
    legacy_streams: int


class Window(Model):
    start: AwareDatetime
    end: AwareDatetime
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    days: Literal[7, 30] | None = None


class Summary(Model):
    healthy: int = 0
    abnormal: int = 0
    unknown: int = 0
    observed_runtime: int = 0
    expected_runtime: int = 5
    covered_sources: int = 0
    expected_sources: int = 5
    status: Literal['complete', 'partial', 'unavailable'] = 'unavailable'
    usage: Counts


class Trend(Model):
    date: str
    turns: Metric
    answered_turns: Metric
    deliveries: Metric


class Ranking(Model):
    application_id: str
    turns: Metric


class DashboardOverview(Model):
    schema_version: Literal[1] = 1
    generated_at: AwareDatetime
    window: Window
    rolling_24h: Window
    active_window_minutes: Literal[15] = 15
    inventory: Inventory
    summary: Summary
    applications: list[Application]
    channel_agents: list[Channel]
    legacy_streams: list[Channel]
    trend: list[Trend]
    last_24h: Counts
    ranking: list[Ranking]
