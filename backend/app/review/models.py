from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID


FailureLayer = Literal[
    "channel",
    "context",
    "guardrail",
    "schema",
    "planner",
    "capability_evidence",
    "coverage",
    "synthesis",
    "outcome",
    "trace_eval",
]
Disposition = Literal["actionable", "duplicate", "not_actionable", "wont_fix"]
ProgressStatus = Literal[
    "pending_triage",
    "fixing",
    "awaiting_merge",
    "awaiting_deploy",
    "awaiting_replay",
    "awaiting_review",
    "closed",
    "duplicate",
    "not_actionable",
    "wont_fix",
]
SemanticVerdict = Literal["pending", "passed", "failed"]
ReviewMethod = Literal["codex", "human_fae"]


@dataclass(frozen=True)
class IssueRecord:
    id: UUID
    agent_id: str
    title: str
    priority: Literal["P0", "P1", "P2", "P3"]
    failure_layer: FailureLayer | None = None
    secondary_layers: tuple[FailureLayer, ...] = ()
    root_cause: str = ""
    impact_scope: str = ""
    owner: str | None = None
    fix_ready: bool = False
    verified_merge: bool = False
    verified_deployment: bool = False
    merge_ancestor: bool = False
    disposition: Disposition = "actionable"
    previous_status: ProgressStatus | None = None
    row_version: int = 1


@dataclass(frozen=True)
class LinkGate:
    id: UUID
    active: bool = True
    link_role: Literal["primary", "secondary"] = "primary"
    runtime_gate_passed: bool = False
    runtime_failure_reason: str = ""
    build_identity_matches: bool | None = None
    model_echo_available: bool | None = None
    actual_model_matches: bool | None = None
    semantic_verdict: SemanticVerdict = "pending"
    review_method: ReviewMethod | None = None
    reviewer: str | None = None
    review_reason: str = ""


@dataclass(frozen=True)
class IssueProgress:
    issue_id: UUID
    status: ProgressStatus
    missing_gates: list[str] = field(default_factory=list)
    replay_passed_turns: int = 0
    replay_required_turns: int = 0
    reopened: bool = False


@dataclass(frozen=True)
class NegativeFeedbackGroup:
    agent_id: str
    turn_key: str
    question: str
    feedback_keys: tuple[str, ...]


@dataclass(frozen=True)
class BackfillReport:
    baseline_negative_rows: int
    baseline_negative_turns: int
    live_negative_rows: int
    live_negative_turns: int
    delta_negative_rows: int
    delta_negative_turns: int
    created_issues: int
    created_links: int
    created_events: int
    linked_feedback_keys: int


@dataclass(frozen=True)
class IssueTransition:
    issue_id: UUID
    previous_status: ProgressStatus | None
    current_status: ProgressStatus
    changed_at: datetime
