from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from .models import FlywheelFilters, SessionFilters, SourceKind
from .repository import ObservabilityReadError


router = APIRouter(prefix="/api", tags=["observability"])


def _service(request: Request):
    return request.app.state.observability_service


def _unavailable(error: ObservabilityReadError):
    raise HTTPException(status_code=503, detail="observability unavailable") from error


@router.get("/agents")
async def list_agents(request: Request):
    try:
        return await _service(request).list_agents()
    except ObservabilityReadError as error:
        _unavailable(error)


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    try:
        result = await _service(request).get_agent(agent_id)
    except ObservabilityReadError as error:
        _unavailable(error)
    if result is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return result


@router.get("/sessions")
async def list_sessions(
    request: Request,
    agent_id: str | None = None,
    source_kind: SourceKind | None = None,
    channel: str | None = None,
    q: str | None = None,
    sentiment: Literal["positive", "negative", "other"] | None = None,
    review_status: str | None = None,
    outcome: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = SessionFilters(
        agent_id=agent_id, source_kind=source_kind, channel=channel, query=q,
        sentiment=sentiment, review_status=review_status, outcome=outcome,
        date_from=date_from, date_to=date_to,
    )
    try:
        return await _service(request).list_sessions(filters, limit, offset)
    except ObservabilityReadError as error:
        _unavailable(error)


@router.get("/sessions/{session_key}")
async def get_session(session_key: str, request: Request):
    try:
        result = await _service(request).get_session(session_key)
    except ObservabilityReadError as error:
        _unavailable(error)
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    return result


@router.get("/turns/{turn_key}/trace")
async def get_trace(turn_key: str, request: Request):
    try:
        result = await _service(request).get_trace(turn_key)
    except ObservabilityReadError as error:
        _unavailable(error)
    if result is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return result


@router.get("/flywheel/overview")
async def flywheel_overview(request: Request):
    try:
        return await _service(request).flywheel_overview()
    except ObservabilityReadError as error:
        _unavailable(error)


@router.get("/flywheel/items")
async def flywheel_items(
    request: Request,
    agent_id: str | None = None,
    item_type: Literal["evaluation", "knowledge", "qa"] | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = FlywheelFilters(agent_id=agent_id, item_type=item_type, status=status)
    try:
        return await _service(request).list_improvement_items(filters, limit, offset)
    except ObservabilityReadError as error:
        _unavailable(error)


@router.get("/sync/status")
async def sync_status(request: Request):
    try:
        return await _service(request).sync_status()
    except ObservabilityReadError as error:
        _unavailable(error)
