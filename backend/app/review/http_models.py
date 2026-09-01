from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FaeCreateIssue(StrictModel):
    origin_turn_key: str | None = None
    title: str = Field(min_length=1, max_length=240)
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    failure_layer: str | None = None
    secondary_layers: list[str] = Field(default_factory=list)
    root_cause: str = ""
    impact_scope: str = ""
    owner: str | None = None
    reason: str = "issue created"


class CreateIssue(FaeCreateIssue):
    agent_id: str = Field(min_length=1)


class UpdateIssue(StrictModel):
    row_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    failure_layer: str | None = None
    secondary_layers: list[str] | None = None
    root_cause: str | None = None
    impact_scope: str | None = None
    owner: str | None = None
    reason: str = "issue updated"


class FaeLinkTurn(StrictModel):
    source_turn_key: str = Field(min_length=1)
    source_feedback_keys: list[str] = Field(default_factory=list)
    link_role: Literal["primary", "secondary"] = "primary"
    reason: str = "turn linked"


class LinkTurn(FaeLinkTurn):
    agent_id: str = Field(min_length=1)


class MoveLink(StrictModel):
    target_issue_id: UUID
    reason: str = Field(min_length=1)


class MergeIssue(StrictModel):
    target_issue_id: UUID
    row_version: int = Field(ge=1)
    reason: str = Field(min_length=1)


class FixReady(StrictModel):
    row_version: int = Field(ge=1)
    reason: str = Field(min_length=1)


class AddEvidence(StrictModel):
    evidence_type: Literal["commit", "pull_request", "merge", "deployment"]
    repository: str = ""
    reference: str = Field(min_length=1)
    url: str = ""
    version: str = ""
    commit_sha: str = ""
    release_manifest_ref: str = ""
    environment: str = ""
    reason: str = "evidence added"

    @model_validator(mode="after")
    def deployment_is_production_artifact(self):
        if self.evidence_type == "deployment" and (
            self.environment != "production"
            or not self.release_manifest_ref.strip()
        ):
            raise ValueError(
                "deployment evidence requires a production release manifest"
            )
        return self


class VerifyEvidence(StrictModel):
    reason: str = "machine verification requested"


class StartReplay(StrictModel):
    issue_link_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class SemanticReview(StrictModel):
    verdict: Literal["passed", "failed"]
    method: Literal["codex", "human_fae"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def accountable_reviewer(self):
        if self.reviewer.strip() in {"", "web-reviewer", "anonymous"}:
            raise ValueError("accountable reviewer required")
        return self


class SetDisposition(StrictModel):
    disposition: Literal["actionable", "duplicate", "not_actionable", "wont_fix"]
    canonical_issue_id: UUID | None = None
    owner: str | None = None
    row_version: int = Field(ge=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def duplicate_requires_target(self):
        if self.disposition == "duplicate" and self.canonical_issue_id is None:
            raise ValueError("duplicate requires canonical_issue_id")
        if self.disposition != "duplicate" and self.canonical_issue_id is not None:
            raise ValueError("canonical_issue_id is only valid for duplicate")
        return self
