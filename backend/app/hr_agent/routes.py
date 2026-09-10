"""Exact HTTP endpoints; identity, Origin and CSRF use platform middleware."""

import json
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from .types import HrAgentProblem, ResultQuery, problem

PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class HrAgentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secured(request):
            try:
                response = await handler(request)
            except HrAgentProblem as error:
                response = JSONResponse(error.problem, status_code=error.http_status)
            except RequestValidationError:
                response = JSONResponse(
                    problem("invalid_input").problem, status_code=422
                )
            for name, value in PRIVATE_HEADERS.items():
                response.headers[name] = value
            return response

        return secured


def auth(request):
    return getattr(request.state, "auth_context", None)


async def body(request):
    try:
        return json.loads(await request.body())
    except (ValueError, UnicodeError):
        raise problem("invalid_input", http_status=400) from None


def key(request):
    value = request.headers.get("Idempotency-Key", "")
    if not 1 <= len(value) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise problem("invalid_input")
    return value


def build_hr_agent_router(service, results=None, materials=None, standards=None):
    if results is not None:
        service.results = results
    if materials is not None:
        service.materials = materials
    router = APIRouter(prefix="/api/hr/agent", route_class=HrAgentRoute)

    @router.post("/works")
    async def submit(request: Request):
        result = await run_in_threadpool(
            service.submit, auth(request), await body(request), key(request)
        )
        return JSONResponse(
            result, status_code=200 if getattr(result, "replayed", False) else 201
        )

    @router.get("/threads")
    def threads(request: Request, cursor: str | None = None):
        return service.list_threads(auth(request), cursor)

    @router.get("/threads/{thread_id}/works")
    def works(request: Request, thread_id: UUID, cursor: str | None = None):
        return service.list_works(auth(request), thread_id, cursor)

    @router.get("/works/{work_id}")
    def work(request: Request, work_id: UUID):
        return service.get_work(auth(request), work_id)

    @router.get("/works/{work_id}/messages")
    def messages(
        request: Request,
        work_id: UUID,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        return service.list_messages(auth(request), work_id, after, limit)

    @router.get("/works/{work_id}/events")
    def events(
        request: Request,
        work_id: UUID,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        return service.list_events(auth(request), work_id, after, limit)

    @router.post("/works/{work_id}/inputs", status_code=202)
    async def append(request: Request, work_id: UUID):
        return await run_in_threadpool(
            service.append_input,
            auth(request),
            work_id,
            await body(request),
            key(request),
        )

    @router.post("/works/{work_id}/cancel")
    async def cancel(request: Request, work_id: UUID):
        return await run_in_threadpool(
            service.cancel, auth(request), work_id, await body(request), key(request)
        )

    @router.post("/works/{work_id}/budget-extensions")
    async def extend(request: Request, work_id: UUID):
        return await run_in_threadpool(
            service.extend_budget,
            auth(request),
            work_id,
            await body(request),
            key(request),
        )

    @router.get("/materials/{attachment_id}")
    def material(request: Request, attachment_id: UUID):
        return service.resolve_material(auth(request), attachment_id)

    @router.get("/results")
    def results(
        request: Request,
        thread_id: UUID | None = None,
        object_kind: str | None = None,
        object_id: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
    ):
        if (object_kind is None) != (object_id is None):
            raise problem("invalid_input")
        obj = (
            {"kind": object_kind, "id": object_id} if object_kind is not None else None
        )
        if obj is not None:
            from .types import validate_contract

            validate_contract("ObjectRef", obj)
        return service.list_results(
            auth(request), ResultQuery(thread_id, obj, kind, cursor)
        )

    @router.get("/results/{result_id}/revisions/{revision}")
    def result(request: Request, result_id: UUID, revision: UUID):
        return service.read_result(auth(request), result_id, revision)

    @router.get("/results/{result_id}/revisions/{revision}/file-info")
    def result_file_info(request: Request, result_id: UUID, revision: UUID):
        info, _ = service.export_result(auth(request), result_id, revision)
        return info

    @router.get("/results/{result_id}/revisions/{revision}/file")
    def result_file(request: Request, result_id: UUID, revision: UUID):
        info, content = service.export_result(auth(request), result_id, revision)
        return Response(content, media_type="text/markdown", headers={
            "Content-Disposition": f'attachment; filename="{info["filename"]}"',
        })

    @router.post("/results/{result_id}/links")
    async def link(request: Request, result_id: UUID):
        return await run_in_threadpool(
            service.link_result,
            auth(request),
            result_id,
            await body(request),
            key(request),
        )

    @router.post("/positions/{position_id}/standards/confirm")
    def confirm(request: Request, position_id: UUID):
        key(request)
        return service.standards_unavailable(auth(request), True)

    @router.get("/positions/{position_id}/standards/current")
    def standard(request: Request, position_id: UUID):
        return service.standards_unavailable(auth(request))

    return router
