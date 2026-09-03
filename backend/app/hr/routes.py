from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.control_plane.models import AuthContext

from .models import PositionDraftRecord, PositionRecord
from .repository import HrConflict, HrNotFound, HrUnavailable


_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class HrPositionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secure(request: Request):
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_PRIVATE_HEADERS}
                raise
            except RequestValidationError:
                response = JSONResponse(
                    {"detail": "HR position request invalid"}, status_code=422
                )
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


class ProposeDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["historical_conversation", "new_conversation"]
    source_key: str = Field(min_length=1, max_length=256)
    source_conversation_id: UUID | None
    title: str = Field(min_length=1, max_length=500)
    proposal: dict[str, object]
    evidence: dict[str, object]
    discovery_rule_version: str = Field(min_length=1, max_length=128)

    @field_validator("source_key", "title", "discovery_rule_version")
    @classmethod
    def trim_text(cls, value: str) -> str:
        selected = value.strip()
        if not selected or "\0" in selected:
            raise ValueError("HR position text invalid")
        return selected


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_row_version: int = Field(ge=1)


class MergeDraftBody(VersionBody):
    target_position_id: UUID

    @field_validator("target_position_id", mode="before")
    @classmethod
    def parse_target_position_id(cls, value):
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError:
                return value
        return value


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _auth_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication required")
    return context


def _request_id(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(422, "Idempotency-Key must be a UUID") from None


def _position(record: PositionRecord) -> dict[str, object]:
    return {
        "position_id": str(record.position_id),
        "source_kind": record.source_kind,
        "official_job_id": record.official_job_id,
        "title": record.title,
        "department": record.department,
        "locations": list(record.locations),
        "official_status": record.official_status,
        "internal_status": record.internal_status,
        "source_version": record.source_version,
        "row_version": record.row_version,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _draft(record: PositionDraftRecord) -> dict[str, object]:
    return {
        "draft_id": str(record.draft_id),
        "source_kind": record.source_kind,
        "source_key": record.source_key,
        "source_conversation_id": (
            str(record.source_conversation_id)
            if record.source_conversation_id is not None else None
        ),
        "title": record.title,
        "proposal": record.proposal,
        "evidence": record.evidence,
        "discovery_rule_version": record.discovery_rule_version,
        "state": record.state,
        "resolved_position_id": (
            str(record.resolved_position_id)
            if record.resolved_position_id is not None else None
        ),
        "row_version": record.row_version,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _binding(record) -> dict[str, object]:
    created_at = record.created_at
    if not isinstance(created_at, datetime):
        raise HrUnavailable("binding projection invalid")
    return {
        "position_id": str(record.position_id),
        "conversation_id": str(record.conversation_id),
        "binding_kind": record.binding_kind,
        "previous_position_id": (
            str(record.previous_position_id)
            if record.previous_position_id is not None else None
        ),
        "created_at": created_at.isoformat(),
    }


def build_hr_position_router(service, agent_use_authorization) -> APIRouter:
    required_service = (
        "list_positions", "position", "list_drafts", "propose_draft",
        "confirm_draft", "merge_draft", "dismiss_draft", "bind_conversation",
    )
    if any(not callable(getattr(service, name, None)) for name in required_service):
        raise ValueError("HR position service required")
    if not callable(getattr(agent_use_authorization, "decide_for_user_id", None)):
        raise ValueError("HR Agent authorization required")
    router = APIRouter(tags=["hr-positions"], route_class=HrPositionRoute)

    async def owner(request: Request, *, writable: bool = False) -> UUID:
        context = _auth_context(request)
        if writable and context.hard_stale_read_only:
            raise HTTPException(503, "account is read only")
        try:
            decision = await asyncio.to_thread(
                agent_use_authorization.decide_for_user_id,
                context.internal_user_id,
                "hr-bot",
            )
        except AgentUseAuthorizationUnavailable:
            raise HTTPException(503, "HR Agent authorization unavailable") from None
        if not getattr(decision, "allowed", False):
            raise HTTPException(403, "HR Agent use denied")
        return context.internal_user_id

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except HrNotFound:
            raise HTTPException(404, "HR position not found") from None
        except HrConflict:
            raise HTTPException(409, "HR position conflict") from None
        except HrUnavailable:
            raise HTTPException(503, "HR position unavailable") from None

    @router.get("/api/hr/positions")
    async def list_positions(
        request: Request,
        query: Annotated[str | None, Query(max_length=500)] = None,
        source: Annotated[Literal["official_site", "manual"] | None, Query()] = None,
        internal_status: Annotated[
            Literal["draft", "active", "archived"] | None, Query()
        ] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        owner_id = await owner(request)
        page = await call(
            service.list_positions, owner_id, query=query, source=source,
            internal_status=internal_status, cursor=cursor, limit=limit,
        )
        return {"items": [_position(item) for item in page.items], "next_cursor": page.next_cursor}

    @router.get("/api/hr/positions/{position_id}")
    async def position_detail(
        request: Request, position_id: Annotated[UUID, Path()]
    ):
        owner_id = await owner(request)
        detail = await call(service.position, owner_id, position_id)
        return {
            **_position(detail.position),
            "conversation_count": detail.conversation_count,
            "material_count": detail.material_count,
            "artifact_count": detail.artifact_count,
        }

    @router.get("/api/hr/position-drafts")
    async def list_drafts(
        request: Request,
        state: Annotated[
            Literal["proposed", "confirmed", "merged", "dismissed"] | None,
            Query(),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ):
        owner_id = await owner(request)
        records = await call(service.list_drafts, owner_id, state=state, limit=limit)
        return {"items": [_draft(item) for item in records]}

    @router.post("/api/hr/position-drafts")
    async def propose_draft(
        body: ProposeDraftBody,
        request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.propose_draft,
            owner_id=owner_id,
            request_id=_request_id(idempotency_key),
            **body.model_dump(),
        )
        return _draft(record)

    @router.post("/api/hr/position-drafts/{draft_id}/confirm")
    async def confirm_draft(
        body: VersionBody, request: Request, draft_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.confirm_draft, owner_id, draft_id, _request_id(idempotency_key),
            expected_row_version=body.expected_row_version,
        )
        return _position(record)

    @router.post("/api/hr/position-drafts/{draft_id}/merge")
    async def merge_draft(
        body: MergeDraftBody, request: Request, draft_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.merge_draft, owner_id, draft_id, body.target_position_id,
            _request_id(idempotency_key),
            expected_row_version=body.expected_row_version,
        )
        return _draft(record)

    @router.post("/api/hr/position-drafts/{draft_id}/dismiss")
    async def dismiss_draft(
        body: VersionBody, request: Request, draft_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.dismiss_draft, owner_id, draft_id, _request_id(idempotency_key),
            expected_row_version=body.expected_row_version,
        )
        return _draft(record)

    @router.post("/api/hr/positions/{position_id}/conversations/{conversation_id}")
    async def bind_conversation(
        _body: EmptyBody, request: Request,
        position_id: Annotated[UUID, Path()],
        conversation_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.bind_conversation, owner_id, position_id, conversation_id,
            _request_id(idempotency_key), binding_kind="created_in_position",
        )
        return _binding(record)

    return router
