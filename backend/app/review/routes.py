from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.agent_brain.conversation_repository import ConversationRepositoryError

from .http_models import (
    AddEvidence,
    CreateIssue,
    FixReady,
    LinkTurn,
    MergeIssue,
    MoveLink,
    SemanticReview,
    SetDisposition,
    StartReplay,
    StrictModel,
    UpdateIssue,
    VerifyEvidence,
)
from .repository import (
    ConcurrentUpdate,
    InvalidReviewMutation,
    ReviewNotFound,
    ReviewRepositoryError,
)
from .service import ReviewUnavailable


router = APIRouter(prefix="/api/review", tags=["review"])


def review_actor(
    request: Request,
    x_review_actor: str | None = Header(default=None),
) -> str:
    context = getattr(request.state, "auth_context", None)
    if context is not None:
        return f"corp:{context.internal_user_id}"
    if x_review_actor is None:
        raise HTTPException(status_code=422, detail="accountable review actor required")
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


def _conversation_repository(request: Request):
    repository = getattr(request.app.state, "conversation_repository", None)
    if repository is None:
        raise HTTPException(503, "conversation feedback unavailable")
    return repository


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
async def overview(request: Request, agent_id: str | None = None):
    return await _invoke(_service(request).overview(agent_id=agent_id))


@router.get("/inbox")
async def inbox(
    request: Request,
    agent_id: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await _invoke(
        _service(request).inbox(
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/issues")
async def issues(
    request: Request,
    agent_id: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await _invoke(
        _service(request).list_issues(
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/turn-summaries")
async def turn_summaries(
    request: Request,
    turn_key: list[str] = Query(...),
):
    unique_keys = list(dict.fromkeys(turn_key))
    if len(unique_keys) > 200:
        raise HTTPException(status_code=422, detail="at most 200 turn keys")
    return await _invoke(
        _service(request).turn_summaries(turn_keys=unique_keys)
    )


@router.get("/conversation-feedback")
async def conversation_feedback(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        items, total = await asyncio.to_thread(
            _conversation_repository(request).list_feedback,
            limit,
            offset,
        )
    except HTTPException:
        raise
    except ConversationRepositoryError:
        raise HTTPException(503, "conversation feedback unavailable") from None
    return {
        "items": [
            {
                "feedback_id": str(item.feedback_id),
                "conversation_id": str(item.conversation_id),
                "message_id": str(item.message_id),
                "turn_id": str(item.turn_id),
                "mission_id": str(item.mission_id) if item.mission_id else None,
                "rating": item.rating,
                "reason": item.reason,
                "comment": item.comment,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


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
