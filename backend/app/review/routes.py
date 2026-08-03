from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .repository import (
    ConcurrentUpdate,
    InvalidReviewMutation,
    ReviewNotFound,
    ReviewRepositoryError,
)
from .service import ReviewUnavailable


router = APIRouter(prefix="/api/review", tags=["review"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIssue(StrictModel):
    agent_id: str = Field(min_length=1)
    origin_turn_key: str | None = None
    title: str = Field(min_length=1, max_length=240)
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    failure_layer: str | None = None
    secondary_layers: list[str] = Field(default_factory=list)
    root_cause: str = ""
    impact_scope: str = ""
    owner: str | None = None
    reason: str = "issue created"


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


class LinkTurn(StrictModel):
    agent_id: str = Field(min_length=1)
    source_turn_key: str = Field(min_length=1)
    source_feedback_keys: list[str] = Field(min_length=1)
    link_role: Literal["primary", "secondary"] = "primary"
    reason: str = "turn linked"


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
    observed_at: datetime | None = None
    reason: str = "evidence added"


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
        return self


def review_actor(x_review_actor: str = Header(...)) -> str:
    actor = x_review_actor.strip()
    if actor == "codex":
        return actor
    if actor.startswith("fae:") and actor.removeprefix("fae:").strip():
        return actor
    if actor.startswith("corp:") and actor.removeprefix("corp:").strip():
        return actor
    raise HTTPException(status_code=422, detail="accountable review actor required")


Actor = Annotated[str, Depends(review_actor)]


def _service(request: Request):
    return request.app.state.review_service


async def _invoke(awaitable):
    try:
        return await awaitable
    except ConcurrentUpdate as error:
        raise HTTPException(
            status_code=409,
            detail={"message": str(error), "current": error.current},
        ) from error
    except ReviewNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidReviewMutation as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ReviewUnavailable, ReviewRepositoryError) as error:
        raise HTTPException(status_code=503, detail="feedback review unavailable") from error


@router.get("/overview")
async def overview(request: Request):
    return await _invoke(_service(request).overview())


@router.get("/inbox")
async def inbox(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await _invoke(_service(request).inbox(limit=limit, offset=offset))


@router.get("/issues")
async def issues(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await _invoke(_service(request).list_issues(limit=limit, offset=offset))


@router.get("/issues/{issue_id}")
async def issue_detail(issue_id: UUID, request: Request):
    return await _invoke(_service(request).issue_detail(issue_id))


@router.post("/issues", status_code=201)
async def create_issue(payload: CreateIssue, request: Request, actor: Actor):
    return await _invoke(_service(request).create_issue(payload, actor=actor))


@router.patch("/issues/{issue_id}")
async def update_issue(
    issue_id: UUID,
    payload: UpdateIssue,
    request: Request,
    actor: Actor,
):
    return await _invoke(
        _service(request).update_issue(issue_id, payload, actor=actor)
    )


@router.post("/issues/{issue_id}/links", status_code=201)
async def link_turn(issue_id: UUID, payload: LinkTurn, request: Request, actor: Actor):
    return await _invoke(_service(request).link_turn(issue_id, payload, actor=actor))


@router.post("/issues/{issue_id}/links/{link_id}/move")
async def move_link(
    issue_id: UUID,
    link_id: UUID,
    payload: MoveLink,
    request: Request,
    actor: Actor,
):
    return await _invoke(
        _service(request).move_link(
            issue_id,
            link_id,
            payload,
            actor=actor,
        )
    )


@router.post("/issues/{issue_id}/merge")
async def merge_issue(
    issue_id: UUID,
    payload: MergeIssue,
    request: Request,
    actor: Actor,
):
    return await _invoke(_service(request).merge_issue(issue_id, payload, actor=actor))


@router.post("/issues/{issue_id}/fix-ready")
async def fix_ready(
    issue_id: UUID,
    payload: FixReady,
    request: Request,
    actor: Actor,
):
    return await _invoke(_service(request).mark_fix_ready(issue_id, payload, actor=actor))


@router.post("/issues/{issue_id}/evidence", status_code=201)
async def add_evidence(
    issue_id: UUID,
    payload: AddEvidence,
    request: Request,
    actor: Actor,
):
    return await _invoke(_service(request).add_evidence(issue_id, payload, actor=actor))


@router.post("/evidence/{evidence_id}/verify")
async def verify_evidence(
    evidence_id: UUID,
    payload: VerifyEvidence,
    request: Request,
    actor: Actor,
):
    return await _invoke(
        _service(request).verify_evidence(evidence_id, payload, actor=actor)
    )


@router.post("/issues/{issue_id}/replays", status_code=201)
async def start_replay(
    issue_id: UUID,
    payload: StartReplay,
    request: Request,
    actor: Actor,
):
    return await _invoke(_service(request).start_replay(issue_id, payload, actor=actor))


@router.post("/replays/{replay_id}/semantic-review")
async def semantic_review(
    replay_id: UUID,
    payload: SemanticReview,
    request: Request,
    actor: Actor,
):
    return await _invoke(
        _service(request).semantic_review(replay_id, payload, actor=actor)
    )


@router.post("/issues/{issue_id}/disposition")
async def set_disposition(
    issue_id: UUID,
    payload: SetDisposition,
    request: Request,
    actor: Actor,
):
    return await _invoke(
        _service(request).set_disposition(issue_id, payload, actor=actor)
    )
