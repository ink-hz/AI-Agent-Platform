import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from app.observability.models import Page

from .models import (
    EventFilters,
    EventSeverity,
    OperationsBrief,
    OperationalEvent,
)


router = APIRouter(prefix="/api/operations", tags=["operations"])


def _service(request: Request):
    service = request.app.state.operations_service
    if service is None:
        raise HTTPException(status_code=503, detail="operations unavailable")
    return service


@router.get("/brief", response_model=OperationsBrief)
async def brief(request: Request):
    return await asyncio.to_thread(_service(request).brief)


@router.get("/events", response_model=Page[OperationalEvent])
async def events(
    request: Request,
    agent_id: str | None = None,
    event_type: str | None = None,
    severity: EventSeverity | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = EventFilters(
        agent_id=agent_id,
        event_type=event_type,
        severity=severity,
        date_from=date_from,
        date_to=date_to,
    )
    return await asyncio.to_thread(
        _service(request).list_events,
        filters,
        limit,
        offset,
    )
