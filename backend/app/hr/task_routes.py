from __future__ import annotations

import asyncio
import inspect
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .task_service import (
    CANDIDATE_TASK_KINDS,
    POSITION_TASK_KINDS,
    HrPositionTask,
    HrPositionTaskConflict,
    HrPositionTaskNotFound,
    HrPositionTaskUnavailable,
)

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class HrPositionTaskRoute(APIRoute):
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
                    {"detail": "HR position task request invalid"},
                    status_code=422,
                )
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


class StartPositionTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_kind: Literal[
        "jd",
        "jr",
        "talent_profile",
        "sourcing_strategy",
        "position_interview_plan",
        "candidate_match",
        "candidate_interview_plan",
    ]
    context_version_id: UUID | None = None
    candidate_id: UUID | None = None
    position_candidate_id: UUID | None = None
    material_ids: list[UUID] = Field(default_factory=list, max_length=100)
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def validate_envelope(self):
        candidate_pair = (
            self.candidate_id is not None and self.position_candidate_id is not None
        )
        if (
            len(self.material_ids) != len(set(self.material_ids))
            or (self.candidate_id is None) != (self.position_candidate_id is None)
            or (
                self.task_kind in CANDIDATE_TASK_KINDS
                and (not candidate_pair or self.context_version_id is None)
            )
            or (self.task_kind in POSITION_TASK_KINDS and candidate_pair)
        ):
            raise ValueError("HR position task envelope invalid")
        self.material_ids.sort(key=str)
        return self


def _request_id(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(422, "Idempotency-Key must be a UUID") from None


def _task(record: HrPositionTask) -> dict[str, object]:
    return {
        "task_id": str(record.task_id),
        "task_kind": record.task_kind,
        "status": record.status,
        "error": record.error,
        "conversation_id": (
            str(record.conversation_id) if record.conversation_id else None
        ),
        "turn_id": str(record.turn_id) if record.turn_id else None,
        "candidate_id": str(record.candidate_id) if record.candidate_id else None,
        "position_candidate_id": (
            str(record.position_candidate_id) if record.position_candidate_id else None
        ),
        "references": [{
            "source_type": reference.source_type,
            "source_id": str(reference.source_id),
            "display_label": reference.display_label,
            "version": reference.version,
            "selected_reason": reference.selected_reason,
            "freshness": reference.freshness,
        } for reference in record.references],
    }


def build_hr_position_task_router(service, require_hr_access) -> APIRouter:
    if any(
        not callable(getattr(service, name, None))
        for name in ("start", "recoverable", "get")
    ):
        raise ValueError("HR position task service required")
    if not callable(require_hr_access):
        raise TypeError("HR access dependency required")
    router = APIRouter(
        tags=["hr-position-tasks"],
        route_class=HrPositionTaskRoute,
    )

    async def owner(request: Request, *, writable: bool = False) -> UUID:
        result = require_hr_access(request, writable=writable)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, UUID):
            raise HTTPException(401, "authentication required")
        return result

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except HrPositionTaskNotFound:
            raise HTTPException(404, "HR position task not found") from None
        except HrPositionTaskConflict:
            raise HTTPException(409, "HR position task conflict") from None
        except HrPositionTaskUnavailable:
            raise HTTPException(503, "HR position task unavailable") from None

    @router.post("/api/hr/positions/{position_id}/tasks", status_code=202)
    async def start_task(
        body: StartPositionTaskBody,
        request: Request,
        response: Response,
        position_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.start,
            owner_id=owner_id,
            position_id=position_id,
            request_id=_request_id(idempotency_key),
            task_kind=body.task_kind,
            context_version_id=body.context_version_id,
            material_ids=tuple(body.material_ids),
            conversation_id=body.conversation_id,
            candidate_id=body.candidate_id,
            position_candidate_id=body.position_candidate_id,
        )
        response.status_code = 202
        return _task(record)

    @router.get("/api/hr/positions/{position_id}/tasks")
    async def recover_tasks(
        request: Request,
        position_id: Annotated[UUID, Path()],
        status: Annotated[Literal["active"], Query()] = "active",
    ):
        del status  # active means active plus recent terminal rows for reconciliation.
        owner_id = await owner(request)
        records = await call(service.recoverable, owner_id, position_id)
        return {"items": [_task(record) for record in records]}

    @router.get("/api/hr/positions/{position_id}/tasks/{task_id}")
    async def task_status(
        request: Request,
        position_id: Annotated[UUID, Path()],
        task_id: Annotated[UUID, Path()],
    ):
        owner_id = await owner(request)
        record = await call(service.get, owner_id, position_id, task_id)
        return _task(record)

    return router
