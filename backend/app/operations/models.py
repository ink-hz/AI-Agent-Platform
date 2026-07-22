from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


EventSeverity = Literal["info", "attention", "critical"]
EventStatus = Literal["active", "resolved", "historical"]
EventFamily = Literal["runtime", "data", "execution", "usage", "lifecycle", "recovery"]
AgentVisibility = Literal["business", "system"]


class NewOperationalEvent(BaseModel):
    agent_id: str | None
    agent_visibility: AgentVisibility
    event_type: str
    event_family: EventFamily
    severity: EventSeverity
    title: str
    summary: str
    source_kind: str
    occurred_at: datetime
    facts: dict = Field(default_factory=dict)
    target_kind: str | None = None
    target_id: str | None = None
    target_path: str | None = None
    fingerprint: str


class OperationalEvent(NewOperationalEvent):
    event_id: str
    status: EventStatus
    first_observed_at: datetime
    last_observed_at: datetime
    resolved_at: datetime | None = None


class RuleState(BaseModel):
    rule_key: str
    value: dict
    updated_at: datetime


class RunHealth(BaseModel):
    run_name: str
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    cursor: dict = Field(default_factory=dict)
    error_summary: str | None = None


class EventFilters(BaseModel):
    agent_id: str | None = None
    event_type: str | None = None
    severity: EventSeverity | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class UsageOccurrence(BaseModel):
    turn_key: str
    agent_id: str
    source_kind: str
    occurred_at: datetime


class UsageObservation(BaseModel):
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    bucket_start: datetime
    conversations: int
    cumulative_conversations: int
    occurrences: tuple[UsageOccurrence, ...]


class LifecycleObservation(BaseModel):
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    live_since: datetime | None
    last_updated_at: datetime | None
    observed_at: datetime


class ExecutionObservation(BaseModel):
    turn_key: str
    session_key: str
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    signal_type: Literal["tool_error", "fallback", "empty_answer", "incomplete"]
    occurred_at: datetime
