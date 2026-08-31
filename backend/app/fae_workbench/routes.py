from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.control_plane.audit import AuditCommand, AuditUnavailableError
from app.control_plane.models import AuthContext, Role
from app.observability.models import SessionFilters
from app.observability.repository import ObservabilityReadError


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
