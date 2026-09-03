from __future__ import annotations

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.agent_brain.conversation_repository import ConversationRepositoryError
from app.attachments.download_service import DownloadError
from app.control_plane.models import Role

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


class ConversationFeedbackTriage(StrictModel):
    triage_status: Literal["triaged", "dismissed"]


class ConversationAttachmentTicket(StrictModel):
    purpose: Literal["preview", "download"]


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


def _platform_owner(request: Request) -> UUID:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(401, "authentication required")
    if context.role is not Role.PLATFORM_OWNER:
        raise HTTPException(403, "platform owner required")
    return context.internal_user_id


def _conversation_download_service(request: Request):
    service = getattr(request.app.state, "conversation_attachment_download_service", None)
    if service is None:
        raise HTTPException(503, "conversation attachment unavailable")
    return service


def _citation_payload(item) -> dict[str, object]:
    return {
        "citation_key": item.citation_key,
        "title": item.title,
        "url": item.url,
        "site": item.site,
        "retrieved_at": item.retrieved_at.isoformat(),
        "supports": list(item.supports),
    }


def _feedback_payload(item) -> dict[str, object]:
    feedback = getattr(item, "feedback", item)
    payload: dict[str, object] = {
        "feedback_id": str(feedback.feedback_id),
        "conversation_id": str(feedback.conversation_id),
        "message_id": str(feedback.message_id),
        "turn_id": str(feedback.turn_id),
        "mission_id": str(feedback.mission_id) if feedback.mission_id else None,
        "rating": feedback.rating,
        "reason": feedback.reason,
        "comment": feedback.comment,
        "triage_status": getattr(feedback, "triage_status", None),
        "triaged_by_internal_user_id": (
            str(feedback.triaged_by_internal_user_id)
            if getattr(feedback, "triaged_by_internal_user_id", None)
            else None
        ),
        "triaged_at": (
            feedback.triaged_at.isoformat()
            if getattr(feedback, "triaged_at", None)
            else None
        ),
        "created_at": feedback.created_at.isoformat(),
    }
    if hasattr(item, "question"):
        payload.update({
            "agent_id": item.agent_id,
            "conversation_title": item.conversation_title,
            "question": item.question,
            "answer": item.answer,
            "citations": [_citation_payload(citation) for citation in item.citations],
        })
    return payload


def _review_attachment_payload(item) -> dict[str, object]:
    attachment = item.attachment
    return {
        "attachment_id": str(attachment.attachment_id),
        "conversation_id": str(attachment.conversation_id),
        "source": attachment.source,
        "display_name": attachment.display_name,
        "detected_mime": attachment.detected_mime,
        "size_bytes": attachment.size_bytes,
        "state": attachment.state,
        "created_at": attachment.created_at.isoformat(),
        "retained_until": attachment.retained_until.isoformat(),
        "processing_coverage": attachment.processing_coverage,
        "availability_reason": attachment.availability_reason,
        "artifact_key": item.artifact_key,
        "version_no": item.version_no,
        "current": item.current,
    }


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
    triage_status: Literal["pending_triage", "triaged", "dismissed"] | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _platform_owner(request)
    try:
        items, total = await asyncio.to_thread(
            _conversation_repository(request).list_feedback_for_review,
            triage_status=triage_status,
            limit=limit,
            offset=offset,
        )
    except HTTPException:
        raise
    except ConversationRepositoryError:
        raise HTTPException(503, "conversation feedback unavailable") from None
    return {
        "items": [_feedback_payload(item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/conversation-feedback/{feedback_id}")
async def triage_conversation_feedback(
    feedback_id: UUID,
    payload: ConversationFeedbackTriage,
    request: Request,
):
    actor_id = _platform_owner(request)
    try:
        item = await asyncio.to_thread(
            _conversation_repository(request).triage_feedback,
            actor_id,
            feedback_id,
            payload.triage_status,
        )
    except ConversationRepositoryError:
        raise HTTPException(503, "conversation feedback unavailable") from None
    return _feedback_payload(item)


@router.get("/conversations/{conversation_id}/attachments")
async def review_conversation_attachments(conversation_id: UUID, request: Request):
    _platform_owner(request)
    try:
        items = await asyncio.to_thread(
            _conversation_repository(request).review_conversation_attachments,
            conversation_id,
        )
    except ConversationRepositoryError:
        raise HTTPException(503, "conversation attachment unavailable") from None
    return [_review_attachment_payload(item) for item in items]


@router.post("/attachments/{attachment_id}/ticket")
async def review_attachment_ticket(
    attachment_id: UUID,
    payload: ConversationAttachmentTicket,
    request: Request,
):
    actor_id = _platform_owner(request)
    try:
        ticket = await asyncio.to_thread(
            _conversation_download_service(request).issue_review_ticket,
            actor_id,
            attachment_id,
            payload.purpose,
        )
    except DownloadError:
        raise HTTPException(404, "attachment unavailable") from None
    return {
        "ticket": ticket.ticket,
        "expires_at": ticket.expires_at.isoformat(),
        "content_path": ticket.content_path,
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
