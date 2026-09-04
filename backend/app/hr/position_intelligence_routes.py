from __future__ import annotations

import asyncio
import inspect
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .position_intelligence_models import CONTEXT_MODULES
from .position_intelligence_repository import (
    PositionContextConflict,
    PositionContextNotFound,
    PositionIntelligenceUnavailable,
)

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class PositionIntelligenceRoute(APIRoute):
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
                    {"detail": "HR position context request invalid"},
                    status_code=422,
                )
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


class CreateContextDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_context_version_id: UUID | None
    official_version_id: UUID | None
    modules: dict[str, object]
    summary: str = Field(min_length=1, max_length=32768)
    source_conversation_id: UUID | None = None
    source_turn_id: UUID | None = None
    source_artifact_version_id: UUID | None = None
    source_material_attachment_ids: list[UUID] = Field(default_factory=list, max_length=100)
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_version: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("summary", "agent_id", "model_version")
    @classmethod
    def trim_text(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\0" in normalized:
            raise ValueError("context text invalid")
        return normalized

    @model_validator(mode="after")
    def validate_modules_and_sources(self):
        try:
            encoded = json.dumps(
                self.modules,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("context modules invalid") from None
        if (
            not self.modules
            or not set(self.modules).issubset(CONTEXT_MODULES)
            or any(type(value) is not dict for value in self.modules.values())
            or len(encoded) > 512 * 1024
            or len(set(self.source_material_attachment_ids))
            != len(self.source_material_attachment_ids)
            or (self.source_turn_id is not None and self.source_conversation_id is None)
        ):
            raise ValueError("context modules invalid")
        return self


class ConfirmContextModulesBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_current_context_version_id: UUID | None
    expected_draft_row_version: int = Field(ge=1)
    module_names: list[str] = Field(min_length=1, max_length=8)

    @field_validator("expected_current_context_version_id", mode="before")
    @classmethod
    def parse_optional_uuid(cls, value):
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError:
                return value
        return value

    @field_validator("module_names")
    @classmethod
    def validate_module_names(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or not set(values).issubset(CONTEXT_MODULES):
            raise ValueError("confirmed modules invalid")
        return values


def _request_id(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(422, "Idempotency-Key must be a UUID") from None


def _context(record) -> dict[str, object]:
    return {
        "context_version_id": str(record.context_version_id),
        "position_id": str(record.position_id),
        "version_number": record.version_number,
        "state": record.state,
        "modules": record.modules,
        "summary": record.summary,
        "official_version_id": (
            str(record.official_version_id)
            if record.official_version_id is not None else None
        ),
        "base_context_version_id": (
            str(record.base_context_version_id)
            if record.base_context_version_id is not None else None
        ),
        "source_conversation_id": (
            str(record.source_conversation_id)
            if record.source_conversation_id is not None else None
        ),
        "source_turn_id": (
            str(record.source_turn_id) if record.source_turn_id is not None else None
        ),
        "source_artifact_version_id": (
            str(record.source_artifact_version_id)
            if record.source_artifact_version_id is not None else None
        ),
        "source_material_attachment_ids": [
            str(value) for value in record.source_material_attachment_ids
        ],
        "agent_id": record.agent_id,
        "model_version": record.model_version,
        "created_by": str(record.created_by),
        "confirmed_by": (
            str(record.confirmed_by) if record.confirmed_by is not None else None
        ),
        "created_at": record.created_at.isoformat(),
        "confirmed_at": (
            record.confirmed_at.isoformat()
            if record.confirmed_at is not None else None
        ),
        "row_version": record.row_version,
    }


def _official(record) -> dict[str, object]:
    return {
        "official_position_version_id": str(record.official_position_version_id),
        "position_id": str(record.position_id),
        "official_job_id": record.official_job_id,
        "title": record.title,
        "department": record.department,
        "locations": list(record.locations),
        "category": record.category,
        "subcategory": record.subcategory,
        "headcount": record.headcount,
        "degree": record.degree,
        "employment_type": record.employment_type,
        "salary": record.salary,
        "duty": record.duty,
        "requirement": record.requirement,
        "source_version": record.source_version,
        "source_changed_at": record.source_changed_at.isoformat(),
        "content_hash": record.content_hash,
        "first_observed_at": record.first_observed_at.isoformat(),
        "last_observed_at": record.last_observed_at.isoformat(),
        "official_status": record.official_status,
        "status_reason": record.status_reason,
        "created_at": record.created_at.isoformat(),
    }


def build_position_intelligence_router(service, require_hr_access) -> APIRouter:
    for name in (
        "current",
        "history",
        "drafts",
        "create_draft",
        "confirm_modules",
        "compare",
        "official_versions",
        "official_version",
    ):
        if not callable(getattr(service, name, None)):
            raise ValueError("position intelligence service required")
    if not callable(require_hr_access):
        raise ValueError("HR access dependency required")
    router = APIRouter(
        tags=["hr-position-intelligence"],
        route_class=PositionIntelligenceRoute,
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
        except PositionContextNotFound:
            raise HTTPException(404, "HR position context not found") from None
        except PositionContextConflict:
            raise HTTPException(409, "HR position context conflict") from None
        except PositionIntelligenceUnavailable:
            raise HTTPException(503, "HR position context unavailable") from None

    @router.get("/api/hr/positions/{position_id}/official-versions")
    async def official_versions(
        request: Request, position_id: Annotated[UUID, Path()]
    ):
        owner_id = await owner(request)
        records = await call(service.official_versions, owner_id, position_id)
        return {"items": [_official(record) for record in records]}

    @router.get(
        "/api/hr/positions/{position_id}/official-versions/{official_version_id}"
    )
    async def official_version(
        request: Request,
        position_id: Annotated[UUID, Path()],
        official_version_id: Annotated[UUID, Path()],
    ):
        owner_id = await owner(request)
        record = await call(
            service.official_version,
            owner_id,
            position_id,
            official_version_id,
        )
        return _official(record)

    @router.get("/api/hr/positions/{position_id}/context")
    async def current_context(
        request: Request, position_id: Annotated[UUID, Path()]
    ):
        owner_id = await owner(request)
        current, drafts = await asyncio.gather(
            call(service.current, owner_id, position_id),
            call(service.drafts, owner_id, position_id),
        )
        return {
            "current": _context(current) if current is not None else None,
            "drafts": [_context(record) for record in drafts],
        }

    @router.get("/api/hr/positions/{position_id}/context/versions")
    async def context_versions(
        request: Request, position_id: Annotated[UUID, Path()]
    ):
        owner_id = await owner(request)
        records = await call(service.history, owner_id, position_id)
        return {"items": [_context(record) for record in records]}

    @router.post("/api/hr/positions/{position_id}/context/drafts")
    async def create_context_draft(
        body: CreateContextDraftBody,
        request: Request,
        position_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        values = body.model_dump()
        values["source_material_attachment_ids"] = tuple(
            values["source_material_attachment_ids"]
        )
        record = await call(
            service.create_draft,
            owner_id=owner_id,
            position_id=position_id,
            request_id=_request_id(idempotency_key),
            created_by=owner_id,
            **values,
        )
        return _context(record)

    @router.post(
        "/api/hr/positions/{position_id}/context/drafts/{draft_context_version_id}/confirm"
    )
    async def confirm_context_modules(
        body: ConfirmContextModulesBody,
        request: Request,
        position_id: Annotated[UUID, Path()],
        draft_context_version_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        owner_id = await owner(request, writable=True)
        record = await call(
            service.confirm_modules,
            owner_id=owner_id,
            position_id=position_id,
            draft_context_version_id=draft_context_version_id,
            request_id=_request_id(idempotency_key),
            expected_current_context_version_id=(
                body.expected_current_context_version_id
            ),
            expected_draft_row_version=body.expected_draft_row_version,
            module_names=tuple(body.module_names),
            confirmed_by=owner_id,
        )
        return _context(record)

    @router.get("/api/hr/positions/{position_id}/context/compare")
    async def compare_contexts(
        request: Request,
        position_id: Annotated[UUID, Path()],
        left: Annotated[UUID, Query()],
        right: Annotated[UUID, Query()],
    ):
        owner_id = await owner(request)
        comparison = await call(
            service.compare, owner_id, position_id, left, right
        )
        return comparison

    return router
