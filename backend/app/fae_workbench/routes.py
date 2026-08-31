from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.control_plane.audit import AuditCommand, AuditUnavailableError
from app.control_plane.models import AuthContext, Role
from app.observability.models import SessionFilters
from app.observability.repository import ObservabilityReadError
from app.review.http_models import (
    AddEvidence,
    FaeCreateIssue,
    FaeLinkTurn,
    FixReady,
    MergeIssue,
    MoveLink,
    SemanticReview,
    SetDisposition,
    StartReplay,
    UpdateIssue,
    VerifyEvidence,
)
from app.review.repository import (
    ConcurrentUpdate,
    InvalidReviewMutation,
    ReviewNotFound,
    ReviewRepositoryError,
)
from app.review.service import ReviewUnavailable

from .repository import FaeWorkbenchReadError


IssueLifecycleFilter = Literal[
    "open",
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
IssueDispositionFilter = Literal[
    "actionable", "duplicate", "not_actionable", "wont_fix"
]


def _management_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if context.role not in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
        raise HTTPException(status_code=403, detail="management role required")
    return context


router = APIRouter(
    prefix="/api/admin/fae",
    tags=["fae-workbench"],
    dependencies=[Depends(_management_context)],
)


def _fae_actor(request: Request) -> str:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return f"corp:{context.internal_user_id}"


FaeActor = Annotated[str, Depends(_fae_actor)]


async def _invoke_review(awaitable):
    try:
        return await awaitable
    except ConcurrentUpdate as error:
        raise HTTPException(
            status_code=409,
            detail={"message": str(error), "current": error.current},
        ) from error
    except ReviewNotFound as error:
        raise HTTPException(status_code=404, detail="fae resource not found") from error
    except InvalidReviewMutation as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ReviewUnavailable, ReviewRepositoryError) as error:
        raise HTTPException(
            status_code=503, detail="feedback review unavailable"
        ) from error
    except FaeWorkbenchReadError as error:
        raise HTTPException(
            status_code=503, detail="fae workbench unavailable"
        ) from error


def _required_audit(request: Request):
    writer = getattr(request.app.state, "fae_session_read_audit", None)
    if writer is None:
        raise HTTPException(status_code=503, detail="required audit unavailable")
    return writer


def _append_audit(writer, command: AuditCommand):
    try:
        return writer.append(command)
    except (AuditUnavailableError, RuntimeError, ValueError):
        raise HTTPException(
            status_code=503, detail="required audit unavailable"
        ) from None


def _append_audit_outcome(writer, requested, event_id, **kwargs) -> None:
    try:
        writer.append_outcome(requested, event_id, **kwargs)
    except (AuditUnavailableError, RuntimeError, ValueError):
        raise HTTPException(
            status_code=503, detail="required audit unavailable"
        ) from None


@router.get("/overview")
async def overview(request: Request):
    return await request.app.state.fae_workbench_service.overview(
        datetime.now(timezone.utc)
    )


@router.get("/sessions")
async def sessions(
    request: Request,
    q: str | None = None,
    channel: str | None = None,
    sentiment: Literal["positive", "negative", "other"] | None = None,
    review_status: str | None = None,
    outcome: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    date_before: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = SessionFilters(
        query=q,
        channel=channel,
        sentiment=sentiment,
        review_status=review_status,
        outcome=outcome,
        date_from=date_from,
        date_to=date_to,
        date_before=date_before,
    )
    return await request.app.state.fae_workbench_service.list_sessions(
        filters, limit, offset
    )


@router.get("/sessions/{session_key}")
async def session_detail(session_key: str, request: Request):
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(status_code=401, detail="authentication required")
    operation_id = uuid4()
    requested = AuditCommand(
        event_type="fae_session_detail_read_requested",
        actor_internal_user_id=context.internal_user_id,
        target_type="fae_session",
        target_id=hashlib.sha256(session_key.encode("utf-8")).hexdigest(),
        request_id=operation_id,
        reason="privileged_read",
        metadata={
            "operation_id": str(operation_id),
            "result": "requested",
        },
    )
    audit = _required_audit(request)
    requested_event_id = _append_audit(audit, requested)
    try:
        result = await request.app.state.fae_workbench_service.get_session(
            session_key
        )
    except ObservabilityReadError:
        _append_audit_outcome(
            audit,
            requested,
            requested_event_id,
            error_code="control_unavailable",
        )
        raise HTTPException(
            status_code=503, detail="observability unavailable"
        ) from None
    except Exception:
        _append_audit_outcome(
            audit,
            requested,
            requested_event_id,
            error_code="control_unavailable",
        )
        raise HTTPException(
            status_code=503, detail="observability unavailable"
        ) from None
    if result is None:
        _append_audit_outcome(
            audit,
            requested,
            requested_event_id,
            error_code="business_rejected",
        )
        raise HTTPException(status_code=404, detail="session not found")
    _append_audit_outcome(
        audit,
        requested,
        requested_event_id,
        actual={"operation_id": str(operation_id)},
    )
    return result


@router.get("/issue-overview")
async def issue_overview(request: Request):
    return await _invoke_review(
        request.app.state.fae_workbench_service.issue_overview()
    )


@router.get("/issue-inbox")
async def issue_inbox(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.issue_inbox(
            limit=limit,
            offset=offset,
        )
    )


@router.get("/issues")
async def issues(
    request: Request,
    status: IssueLifecycleFilter | None = None,
    disposition: IssueDispositionFilter | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.list_issues(
            limit=limit,
            offset=offset,
            status=status,
            disposition=disposition,
        )
    )


@router.get("/issues/{issue_id}")
async def issue_detail(issue_id: UUID, request: Request):
    return await _invoke_review(
        request.app.state.fae_workbench_service.issue_detail(issue_id)
    )


@router.get("/turn-summaries")
async def turn_summaries(
    request: Request,
    turn_key: list[str] = Query(...),
):
    unique_keys = list(dict.fromkeys(turn_key))
    if len(unique_keys) > 200:
        raise HTTPException(status_code=422, detail="at most 200 turn keys")
    return await _invoke_review(
        request.app.state.fae_workbench_service.turn_summaries(unique_keys)
    )


@router.post("/issues", status_code=201)
async def create_issue(
    payload: FaeCreateIssue,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.create_issue(payload, actor=actor)
    )


@router.patch("/issues/{issue_id}")
async def update_issue(
    issue_id: UUID,
    payload: UpdateIssue,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.update_issue(
            issue_id, payload, actor=actor
        )
    )


@router.post("/issues/{issue_id}/links", status_code=201)
async def link_turn(
    issue_id: UUID,
    payload: FaeLinkTurn,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.link_turn(
            issue_id, payload, actor=actor
        )
    )


@router.post("/issues/{issue_id}/links/{link_id}/move")
async def move_link(
    issue_id: UUID,
    link_id: UUID,
    payload: MoveLink,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.move_link(
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
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.merge_issue(
            issue_id, payload, actor=actor
        )
    )


@router.post("/issues/{issue_id}/fix-ready")
async def fix_ready(
    issue_id: UUID,
    payload: FixReady,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.mark_fix_ready(
            issue_id, payload, actor=actor
        )
    )


@router.post("/issues/{issue_id}/evidence", status_code=201)
async def add_evidence(
    issue_id: UUID,
    payload: AddEvidence,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.add_evidence(
            issue_id, payload, actor=actor
        )
    )


@router.post("/evidence/{evidence_id}/verify")
async def verify_evidence(
    evidence_id: UUID,
    payload: VerifyEvidence,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.verify_evidence(
            evidence_id, payload, actor=actor
        )
    )


@router.post("/issues/{issue_id}/replays", status_code=201)
async def start_replay(
    issue_id: UUID,
    payload: StartReplay,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.start_replay(
            issue_id, payload, actor=actor
        )
    )


@router.post("/replays/{replay_id}/semantic-review")
async def semantic_review(
    replay_id: UUID,
    payload: SemanticReview,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.semantic_review(
            replay_id, payload, actor=actor
        )
    )


@router.post("/issues/{issue_id}/disposition")
async def set_disposition(
    issue_id: UUID,
    payload: SetDisposition,
    request: Request,
    actor: FaeActor,
):
    return await _invoke_review(
        request.app.state.fae_workbench_service.set_disposition(
            issue_id, payload, actor=actor
        )
    )
