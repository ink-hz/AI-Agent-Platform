from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from .panorama_repository import PanoramaConflict, PanoramaNotFound, PanoramaUnavailable

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class HrPanoramaRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secure(request: Request):
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_PRIVATE_HEADERS}
                raise
            except RequestValidationError:
                response = JSONResponse({"detail": "HR panorama request invalid"}, status_code=422)
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


def _value(record: object, name: str) -> object:
    if isinstance(record, Mapping):
        return record[name]
    return getattr(record, name)


def build_panorama_router(service, require_hr_access) -> APIRouter:
    required = ("current_report", "list_reports", "report", "document", "evidence_file")
    if any(not callable(getattr(service, name, None)) for name in required):
        raise ValueError("panorama service required")
    if not callable(require_hr_access):
        raise TypeError("HR access dependency required")

    router = APIRouter(tags=["hr-panorama"], route_class=HrPanoramaRoute)

    async def authorize(request: Request) -> None:
        selected = require_hr_access(request, writable=False)
        if inspect.isawaitable(selected):
            selected = await selected
        if not isinstance(selected, UUID):
            raise HTTPException(401, "authentication required")

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except PanoramaNotFound:
            raise HTTPException(404, "HR panorama not found") from None
        except PanoramaConflict:
            raise HTTPException(409, "HR panorama conflict") from None
        except PanoramaUnavailable:
            raise HTTPException(503, "HR panorama unavailable") from None
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, "HR panorama request invalid") from None

    @router.get("/api/hr/panorama/current")
    async def current_report(request: Request):
        await authorize(request)
        record = await call(service.current_report)
        return Response(status_code=204) if record is None else record

    @router.get("/api/hr/panorama/reports")
    async def list_reports(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 100):
        await authorize(request)
        return {"items": list(await call(service.list_reports, limit=limit))}

    @router.get("/api/hr/panorama/reports/{bundle_id}")
    async def report(request: Request, bundle_id: Annotated[UUID, Path()]):
        await authorize(request)
        return await call(service.report, bundle_id)

    @router.get("/api/hr/panorama/reports/{bundle_id}/export")
    async def export_report(
        request: Request,
        bundle_id: Annotated[UUID, Path()],
        export_format: Annotated[Literal["pdf", "xlsx", "md"], Query(alias="format")],
    ):
        await authorize(request)
        if set(request.query_params) != {"format"}:
            raise HTTPException(422, "HR panorama request invalid")
        selected = await call(service.document, bundle_id, export_format)
        body = _value(selected, "body")
        mime = str(_value(selected, "mime"))
        sha256 = str(_value(selected, "sha256"))
        filename = str(_value(selected, "name"))
        return Response(
            body,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-SHA256": sha256,
            },
        )

    @router.get("/api/hr/panorama/reports/{bundle_id}/evidence/{evidence_sha256}")
    async def download_evidence(
        request: Request,
        bundle_id: Annotated[UUID, Path()],
        evidence_sha256: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
    ):
        await authorize(request)
        selected = await call(service.evidence_file, bundle_id, evidence_sha256)
        body = _value(selected, "body")
        mime = str(_value(selected, "mime"))
        sha256 = str(_value(selected, "sha256"))
        extension = {"application/json": "json", "text/html": "html", "text/plain": "txt"}.get(mime, "bin")
        return Response(
            body,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="source-evidence-{sha256[:12]}.{extension}"',
                "X-Content-SHA256": sha256,
            },
        )

    return router


__all__ = ["build_panorama_router"]
