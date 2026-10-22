"""Employee-scoped SDK views backed by native ZenTao records."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.control_plane.models import AuthContext
from .zentao import (ZenTaoAuthenticationError, ZenTaoProtocolError,
                     ZenTaoRejected, ZenTaoRequestRejected, ZenTaoUnavailable)


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


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


async def _respond(operation):
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
    return JSONResponse(result, headers=_NO_STORE)


def build_sdk_native_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/sdk/native", tags=["sdk-native"])

    @router.get("/requirements")
    async def requirements(request: Request, page: int = Query(1, ge=1, le=1000),
                           view: str = Query("q4", pattern="^(q4|all)$"),
                           query: str = Query("", max_length=200),
                           status: str = Query("", pattern="^(|draft|reviewing|active|closed|changing)$")):
        return await _respond(_service(request).list_requirements(
            _actor(request), page=page, view=view, query=query.strip(), status=status))

    @router.get("/requirements/{story_id}")
    async def requirement(request: Request, story_id: int):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        return await _respond(_service(request).requirement(_actor(request), story_id))

    @router.get("/my-tasks")
    async def my_tasks(request: Request, page: int = Query(1, ge=1, le=1000)):
        return await _respond(_service(request).my_tasks(_actor(request), page=page))

    return router
