"""SDK portal read API; authorization is independent on every request."""

from __future__ import annotations

import inspect
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.control_plane.models import AuthContext, Role
from .repository import ContentUnavailable, DOCUMENT_ID, SdkAdminRepository


_HEADERS = {"Cache-Control": "private, no-store"}
_READ_ROLES = frozenset({"viewer", "contributor", "reviewer", "release_manager"})


class _PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private(request):
            try:
                response = await handler(request)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
            except RequestValidationError:
                response = JSONResponse({"detail": "sdk_admin_query_invalid"}, status_code=422)
            response.headers.update(_HEADERS)
            return response

        return private


async def _allowed(request: Request) -> bool:
    actor = getattr(request.state, "auth_context", None)
    if not isinstance(actor, AuthContext):
        raise HTTPException(401, "authentication_required")
    if actor.role == Role.PLATFORM_OWNER:
        return True
    service = getattr(request.app.state, "sdk_service", None)
    if service is None:
        service = getattr(request.app.state, "sdk_plan_service", None)
    if service is None:
        return False
    try:
        result = await run_in_threadpool(service.me, actor)
        if inspect.isawaitable(result):
            result = await result
    except HTTPException as error:
        if error.status_code == 403:
            return False
        raise HTTPException(503, "sdk_admin_access_unavailable") from None
    except Exception:
        raise HTTPException(503, "sdk_admin_access_unavailable") from None
    return (isinstance(result, dict) and isinstance(result.get("role"), str)
            and result["role"] in _READ_ROLES)


async def _require_access(request: Request) -> None:
    if not await _allowed(request):
        raise HTTPException(403, "sdk_access_required")


def _repository(request: Request):
    repository = getattr(request.app.state, "sdk_admin_repository", None)
    if repository is not None:
        return repository
    root = os.environ.get("SDK_ADMIN_CONTENT_ROOT", "").strip()
    if not root:
        raise HTTPException(503, "sdk_admin_content_unavailable")
    repository = SdkAdminRepository(Path(root))
    request.app.state.sdk_admin_repository = repository
    return repository


async def _read(request: Request, method: str, *args, **kwargs):
    try:
        return await run_in_threadpool(getattr(_repository(request), method), *args, **kwargs)
    except ContentUnavailable:
        raise HTTPException(503, "sdk_admin_content_unavailable") from None


def build_sdk_admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/sdk-admin", tags=["sdk-admin"], route_class=_PrivateRoute)

    @router.get("/access")
    async def access(request: Request):
        return {"allowed": await _allowed(request)}

    @router.get("/catalog")
    async def catalog(request: Request):
        await _require_access(request)
        return await _read(request, "catalog")

    @router.get("/documents")
    async def documents(request: Request, q: str = Query("", max_length=200),
                        kind: str | None = Query(None, max_length=40),
                        group: str | None = Query(None, max_length=160),
                        offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
        await _require_access(request)
        return await _read(request, "documents", q=q, kind=kind, group=group, offset=offset, limit=limit)

    @router.get("/documents/{document_id}")
    async def document(request: Request, document_id: str):
        await _require_access(request)
        if not DOCUMENT_ID.fullmatch(document_id):
            raise HTTPException(404, "sdk_admin_document_not_found")
        result = await _read(request, "document", document_id)
        if result is None:
            raise HTTPException(404, "sdk_admin_document_not_found")
        return result

    @router.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def unknown(request: Request, path: str):
        await _require_access(request)
        raise HTTPException(404, "sdk_admin_route_not_found")

    return router
