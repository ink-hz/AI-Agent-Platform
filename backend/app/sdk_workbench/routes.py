"""Same-origin SDK requirement routes."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.control_plane.models import AuthContext


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequirementBody(StrictBody):
    request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    scenario: str = Field(min_length=1, max_length=4000)
    device_platform: str = Field(min_length=1, max_length=500)
    acceptance: str = Field(min_length=1, max_length=4000)


class TaskBody(StrictBody):
    request_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)


class ReviewBody(StrictBody):
    request_id: UUID
    decision: Literal["approved", "rejected", "needs_changes"]
    reason: str = Field(min_length=1, max_length=2000)


class MemberBody(StrictBody):
    request_id: UUID
    internal_user_id: UUID
    role: Literal["viewer", "contributor", "reviewer", "release_manager"]


class ReconcileBody(StrictBody):
    request_id: UUID
    external_id: int = Field(gt=0)


def _actor(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(401, "authentication_required")
    return context


def _service(request: Request):
    service = getattr(request.app.state, "sdk_service", None)
    if service is None:
        raise HTTPException(503, "sdk_unavailable")
    return service


def build_sdk_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/sdk", tags=["sdk-workbench"])

    @router.get("/me")
    async def me(request: Request):
        return JSONResponse(_service(request).me(_actor(request)), headers=_NO_STORE)

    @router.get("/members")
    async def members(request: Request):
        return JSONResponse(_service(request).list_members(_actor(request)), headers=_NO_STORE)

    @router.post("/members")
    async def grant_member(request: Request, body: MemberBody):
        result = _service(request).grant_member(_actor(request), body.internal_user_id, body.role, body.request_id)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.get("/reconciliation")
    async def reconciliation(request: Request):
        return JSONResponse(_service(request).reconciliation(_actor(request)), headers=_NO_STORE)

    @router.post("/reconciliation/{actor_id}/{target_request_id}/bind")
    async def bind_reconciliation(request: Request, actor_id: UUID, target_request_id: UUID, body: ReconcileBody):
        result = await _service(request).bind_reconciliation(_actor(request), actor_id, target_request_id, body.request_id, body.external_id)
        return JSONResponse(result, headers=_NO_STORE)

    @router.get("/requirements")
    async def list_requirements(request: Request, page: int = Query(1, ge=1, le=1000)):
        return JSONResponse(await _service(request).list_requirements(_actor(request), page), headers=_NO_STORE)

    @router.post("/requirements")
    async def create_requirement(request: Request, body: RequirementBody):
        result = await _service(request).create_requirement(_actor(request), body.request_id, body.title, body.scenario, body.device_platform, body.acceptance)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.get("/requirements/{story_id}")
    async def requirement(request: Request, story_id: int):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        return JSONResponse(await _service(request).requirement(_actor(request), story_id), headers=_NO_STORE)

    @router.post("/requirements/{story_id}/tasks")
    async def create_task(request: Request, story_id: int, body: TaskBody):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        result = await _service(request).create_task(_actor(request), story_id, body.request_id, body.name, body.description)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.post("/requirements/{story_id}/reviews")
    async def review(request: Request, story_id: int, body: ReviewBody):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        result = await _service(request).review(_actor(request), story_id, body.request_id, body.decision, body.reason)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    return router
