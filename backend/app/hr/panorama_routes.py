from __future__ import annotations

import asyncio
import inspect
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .panorama_models import (
    PanoramaReport,
    PanoramaRun,
    PublicJobSnapshot,
    TalentInsightVersion,
    TalentSource,
    thaw_json,
)
from .panorama_repository import (
    PanoramaConflict,
    PanoramaNotFound,
    PanoramaUnavailable,
)

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
                response = JSONResponse(
                    {"detail": "HR panorama request invalid"},
                    status_code=422,
                )
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


class AddCompanyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_name: str = Field(min_length=1, max_length=500)
    aliases: tuple[str, ...] = Field(default=(), max_length=20)
    approved_urls: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("canonical_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip() or "\0" in value:
            raise ValueError("company name invalid")
        return value

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value.strip() or "\0" in value or len(value) > 500 for value in values
        ):
            raise ValueError("company aliases invalid")
        return values

    @field_validator("approved_urls")
    @classmethod
    def validate_url_schemes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.startswith("https://") for value in values):
            raise ValueError("approved URLs invalid")
        return values


class StartRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    conversation_id: UUID | None = None


def _request_id(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(422, "Idempotency-Key must be a UUID") from None


def _owned(record, owner_id: UUID):
    if getattr(record, "owner_id", None) != owner_id:
        raise HTTPException(404, "HR panorama not found")
    return record


def _source(record: TalentSource) -> dict[str, object]:
    return {
        "source_id": str(record.source_id),
        "source_kind": record.source_kind,
        "canonical_name": record.canonical_name,
        "aliases": list(record.aliases),
        "approved_urls": list(record.approved_urls),
        "active": record.active,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _run(record: PanoramaRun) -> dict[str, object]:
    return {
        "run_id": str(record.run_id),
        "selected_source_ids": [str(value) for value in record.selected_source_ids],
        "conversation_id": str(record.conversation_id),
        "state": record.state,
        "error_code": record.error_code,
        "source_failures": thaw_json(record.source_failures),
        "row_version": record.row_version,
        "started_at": (
            record.started_at.isoformat() if record.started_at is not None else None
        ),
        "finished_at": (
            record.finished_at.isoformat() if record.finished_at is not None else None
        ),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _insight(record: TalentInsightVersion) -> dict[str, object]:
    return {
        "insight_version_id": str(record.insight_version_id),
        "run_id": str(record.run_id),
        "version_number": record.version_number,
        "selected_source_ids": [str(value) for value in record.selected_source_ids],
        "snapshot_ids": [str(value) for value in record.snapshot_ids],
        "facts": thaw_json(record.facts),
        "inferences": thaw_json(record.inferences),
        "unknowns": thaw_json(record.unknowns),
        "direction_clusters": thaw_json(record.direction_clusters),
        "summary": record.summary,
        "source_conversation_id": str(record.source_conversation_id),
        "source_turn_id": str(record.source_turn_id),
        "agent_id": record.agent_id,
        "model_version": record.model_version,
        "created_at": record.created_at.isoformat(),
    }


def _snapshot(record: PublicJobSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": str(record.snapshot_id),
        "run_id": str(record.run_id),
        "source_id": str(record.source_id),
        "public_job_key": record.public_job_key,
        "title": record.title,
        "location": record.location,
        "duty_excerpt": record.duty_excerpt,
        "requirement_excerpt": record.requirement_excerpt,
        "source_url": record.source_url,
        "observed_at": record.observed_at.isoformat(),
        "content_sha256": record.content_sha256,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }


def _report(record: PanoramaReport) -> dict[str, object]:
    return {
        "insight": _insight(record.insight),
        "sources": [_source(value) for value in record.sources],
        "snapshots": [_snapshot(value) for value in record.snapshots],
    }


def build_panorama_router(service, require_hr_access) -> APIRouter:
    required = (
        "add_company",
        "list_companies",
        "start_run",
        "run_status",
        "list_reports",
        "report",
    )
    if any(not callable(getattr(service, name, None)) for name in required):
        raise ValueError("panorama service required")
    if not callable(require_hr_access):
        raise TypeError("HR access dependency required")

    router = APIRouter(tags=["hr-panorama"], route_class=HrPanoramaRoute)

    async def owner(request: Request, *, writable: bool = False) -> UUID:
        selected = require_hr_access(request, writable=writable)
        if inspect.isawaitable(selected):
            selected = await selected
        if not isinstance(selected, UUID):
            raise HTTPException(401, "authentication required")
        return selected

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except PanoramaNotFound:
            raise HTTPException(404, "HR panorama not found") from None
        except PanoramaConflict:
            raise HTTPException(409, "HR panorama conflict") from None
        except PanoramaUnavailable:
            raise HTTPException(503, "HR panorama unavailable") from None
        except ValueError:
            raise HTTPException(422, "HR panorama request invalid") from None

    @router.get("/api/hr/panorama/sources")
    async def list_sources(
        request: Request,
        include_inactive: Annotated[bool, Query()] = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ):
        owner_id = await owner(request)
        records = await call(
            service.list_companies,
            owner_id,
            include_inactive=include_inactive,
            limit=limit,
        )
        return {"items": [_source(_owned(record, owner_id)) for record in records]}

    @router.post("/api/hr/panorama/sources")
    async def add_source(
        body: AddCompanyBody,
        request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.add_company,
            owner_id=owner_id,
            request_id=_request_id(idempotency_key),
            canonical_name=body.canonical_name,
            aliases=body.aliases,
            approved_urls=body.approved_urls,
        )
        return _source(_owned(record, owner_id))

    @router.post("/api/hr/panorama/runs", status_code=202)
    async def start_run(
        body: StartRunBody,
        request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.start_run,
            owner_id=owner_id,
            request_id=_request_id(idempotency_key),
            source_ids=body.source_ids,
            conversation_id=body.conversation_id,
        )
        return _run(_owned(record, owner_id))

    @router.get("/api/hr/panorama/runs/{run_id}")
    async def run_status(
        request: Request,
        run_id: Annotated[UUID, Path()],
    ):
        owner_id = await owner(request)
        record = await call(service.run_status, owner_id, run_id)
        return _run(_owned(record, owner_id))

    @router.get("/api/hr/panorama/reports")
    async def list_reports(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ):
        owner_id = await owner(request)
        records = await call(service.list_reports, owner_id, limit=limit)
        return {"items": [_insight(_owned(record, owner_id)) for record in records]}

    @router.get("/api/hr/panorama/reports/{insight_version_id}")
    async def report(
        request: Request,
        insight_version_id: Annotated[UUID, Path()],
    ):
        owner_id = await owner(request)
        record = await call(service.report, owner_id, insight_version_id)
        _owned(record.insight, owner_id)
        return _report(record)

    return router


__all__ = ["build_panorama_router"]
