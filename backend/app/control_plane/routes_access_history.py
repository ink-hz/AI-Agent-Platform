from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .access_history import (
    AccessHistoryFilter,
    AccessHistoryForbidden,
    AccessHistoryInvalid,
    AccessHistoryUnavailable,
    PageAccessDescriptor,
)
from .models import AuthContext, Role


class PageViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_event_id: UUID
    workspace_key: str = Field(min_length=1, max_length=32)
    page_key: str = Field(min_length=1, max_length=96)
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)


class AccessHistoryEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    access_event_id: UUID
    display_name: str
    event_kind: str
    login_kind: str | None
    workspace_key: str | None
    page_key: str | None
    page_display_name: str | None
    agent_id: str | None
    occurred_at: datetime


class AccessHistoryPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AccessHistoryEventResponse]
    limit: int
    offset: int
    has_more: bool


def _context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(status_code=401, detail="authentication required")
    return context


def _owner_context(request: Request) -> AuthContext:
    context = _context(request)
    if context.role is not Role.PLATFORM_OWNER:
        raise HTTPException(status_code=403, detail="platform owner required")
    return context


def build_access_history_router(repository) -> APIRouter:
    router = APIRouter(tags=["access-history"])

    @router.post(
        "/api/v1/access-events/page-view",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def record_page_view(request: Request) -> Response:
        context = _context(request)
        media_type = request.headers.get("content-type", "").split(";", 1)[0]
        if media_type.strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="JSON body required")
        raw = await request.body()
        if len(raw) > 2048:
            raise HTTPException(status_code=413, detail="request body too large")
        try:
            body = PageViewBody.model_validate_json(raw)
        except (ValidationError, ValueError):
            raise HTTPException(
                status_code=400, detail="page access event invalid"
            ) from None
        try:
            outcome = repository.record_page_view(
                body.access_event_id,
                context,
                PageAccessDescriptor(
                    body.workspace_key, body.page_key, body.agent_id
                ),
            )
        except AccessHistoryInvalid:
            raise HTTPException(
                status_code=400, detail="page access event invalid"
            ) from None
        except AccessHistoryForbidden:
            raise HTTPException(
                status_code=403, detail="page access rejected"
            ) from None
        except AccessHistoryUnavailable:
            raise HTTPException(
                status_code=503, detail="page access unavailable"
            ) from None
        if outcome == "rate_limited":
            raise HTTPException(
                status_code=429,
                detail="page access rate exceeded",
                headers={"Retry-After": "60"},
            )
        return Response(status_code=204)

    @router.get(
        "/api/v1/manage/access-events",
        response_model=AccessHistoryPageResponse,
    )
    async def list_access_events(
        request: Request,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        display_name: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        workspace_key: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
        event_kind: Literal["login_succeeded", "page_view"] | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> AccessHistoryPageResponse:
        context = _owner_context(request)
        selected_to = date_to or datetime.now(UTC)
        selected_from = date_from or selected_to - timedelta(days=7)
        filters = AccessHistoryFilter(
            date_from=selected_from,
            date_to=selected_to,
            display_name=display_name,
            workspace_key=workspace_key,
            event_kind=event_kind,
            limit=limit,
            offset=offset,
        )
        try:
            rows = repository.list_events(context, filters)
        except AccessHistoryForbidden:
            raise HTTPException(
                status_code=403, detail="platform owner required"
            ) from None
        except AccessHistoryInvalid:
            raise HTTPException(
                status_code=400, detail="access history query invalid"
            ) from None
        except AccessHistoryUnavailable:
            raise HTTPException(
                status_code=503, detail="访问记录暂不可用"
            ) from None
        return AccessHistoryPageResponse(
            items=[AccessHistoryEventResponse.model_validate(row) for row in rows[:limit]],
            limit=limit,
            offset=offset,
            has_more=len(rows) > limit,
        )

    return router
