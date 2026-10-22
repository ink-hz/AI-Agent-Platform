"""Employee-scoped SDK views backed by native ZenTao records."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.control_plane.models import AuthContext
from .zentao import (ZenTaoAuthenticationError, ZenTaoProtocolError,
                     ZenTaoRejected, ZenTaoRequestRejected, ZenTaoUnavailable)


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ACCOUNT_PATTERN = r"^[a-z][a-z0-9._-]{1,39}$"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequirementCreateBody(StrictBody):
    request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    scenario: str = Field(min_length=1, max_length=16000)
    device_platform: str = Field(min_length=1, max_length=500)
    acceptance: str = Field(min_length=1, max_length=16000)
    priority: int = Field(ge=1, le=4)
    business_proposer: str | None = Field(None, max_length=200)
    reviewer: str | None = Field(None, pattern=_ACCOUNT_PATTERN)
    assigned_to: str | None = Field(None, pattern=_ACCOUNT_PATTERN)


class RequirementEditBody(StrictBody):
    expected_version: int = Field(ge=1)
    expected_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=200)
    scenario: str = Field(min_length=1, max_length=16000)
    device_platform: str | None = Field(None, min_length=1, max_length=500)
    acceptance: str = Field(min_length=1, max_length=16000)
    priority: int = Field(ge=1, le=4)
    business_proposer: str | None = Field(None, max_length=200)


class RequirementAssignBody(StrictBody):
    expected_version: int = Field(ge=1)
    expected_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    assigned_to: str = Field(pattern=_ACCOUNT_PATTERN)


class RequirementReviewBody(StrictBody):
    expected_version: int = Field(ge=1)
    expected_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["pass", "reject"]
    comment: str = Field("", max_length=2000)
    closed_reason: Literal["done", "subdivided", "postponed", "willnotdo", "cancel", "bydesign"] | None = None


def _actor(request: Request) -> AuthContext:
    actor = getattr(request.state, "auth_context", None)
    if actor is None:
        raise HTTPException(401, "authentication_required")
    return actor


def _service(request: Request):
    service = getattr(request.app.state, "sdk_native_service", None)
    if service is None:
        raise HTTPException(503, "sdk_native_unavailable")
    return service


async def _respond(operation, *, success_status: int = 200):
    try:
        result = await operation
    except ZenTaoAuthenticationError:
        raise HTTPException(503, "sdk_zentao_authentication_failed") from None
    except ZenTaoRequestRejected:
        raise HTTPException(403, "sdk_zentao_permission_denied") from None
    except ZenTaoUnavailable:
        raise HTTPException(503, "sdk_zentao_unavailable") from None
    except (ZenTaoProtocolError, ZenTaoRejected):
        raise HTTPException(502, "sdk_zentao_response_invalid") from None
    status_code = 202 if isinstance(result, dict) and result.get("status") == "reconciliation_required" else success_status
    return JSONResponse(result, status_code=status_code, headers=_NO_STORE)


def build_sdk_native_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/sdk/native", tags=["sdk-native"])

    @router.get("/requirements")
    async def requirements(request: Request, page: int = Query(1, ge=1, le=1000),
                           view: str = Query("q4", pattern="^(q4|all)$"),
                           query: str = Query("", max_length=200),
                           owner: str = Query("", max_length=40),
                           status: str = Query("", pattern="^(|draft|reviewing|active|closed|changing)$")):
        return await _respond(_service(request).list_requirements(
            _actor(request), page=page, view=view, query=query.strip(), status=status,
            owner=owner.strip()))

    @router.get("/members")
    async def members(request: Request):
        return await _respond(_service(request).members(_actor(request)))

    @router.post("/requirements")
    async def create_requirement(request: Request, body: RequirementCreateBody):
        return await _respond(_service(request).create_requirement(_actor(request), body.model_dump()),
                              success_status=201)

    @router.get("/requirements/pending-create")
    async def pending_create(request: Request):
        return await _respond(_service(request).pending_create(_actor(request)))

    @router.get("/requirements/{story_id}")
    async def requirement(request: Request, story_id: int):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        return await _respond(_service(request).requirement(_actor(request), story_id))

    @router.put("/requirements/{story_id}")
    async def edit_requirement(request: Request, story_id: int, body: RequirementEditBody):
        return await _respond(_service(request).edit_requirement(
            _actor(request), story_id, **body.model_dump()))

    @router.post("/requirements/{story_id}/assign")
    async def assign_requirement(request: Request, story_id: int, body: RequirementAssignBody):
        return await _respond(_service(request).assign_requirement(
            _actor(request), story_id, **body.model_dump()))

    @router.post("/requirements/{story_id}/review")
    async def review_requirement(request: Request, story_id: int, body: RequirementReviewBody):
        return await _respond(_service(request).review_requirement(
            _actor(request), story_id, **body.model_dump()))

    @router.get("/my-tasks")
    async def my_tasks(request: Request, page: int = Query(1, ge=1, le=1000)):
        return await _respond(_service(request).my_tasks(_actor(request), page=page))

    @router.get("/tasks/{task_id}")
    async def task(request: Request, task_id: int):
        return await _respond(_service(request).task(_actor(request), task_id))

    return router
