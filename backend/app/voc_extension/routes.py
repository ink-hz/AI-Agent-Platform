"""Explicit same-origin BFF routes for the native VOC workspace."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.control_plane.models import AuthContext

from .client import VocProtocolError, VocUpstreamUnavailable

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ALLOWED_STATUSES = frozenset({200, 201, 401, 403, 404, 409, 422, 503})
_VOC_NO_PATTERN = r"^VOC-[0-9]{8}-[0-9]{3,}$"


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftContent(_StrictRequest):
    customer: str | None = Field(default=None, max_length=300)
    feedback: str = Field(min_length=1, max_length=4000)
    product_or_scenario: str | None = Field(default=None, max_length=500)
    impact: str | None = Field(default=None, max_length=500)
    evidence_basis: Literal[
        "customer_quote",
        "employee_observation",
        "employee_relay",
        "unknown",
    ]
    gaps: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("gaps")
    @classmethod
    def validate_gaps(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if (
            any(not value or len(value) > 100 for value in normalized)
            or len(normalized) != len(set(normalized))
        ):
            raise ValueError("VOC gaps are invalid")
        return normalized


class CreateDraftBody(_StrictRequest):
    request_id: UUID
    source_text: str = Field(min_length=1, max_length=4000)


class UpdateDraftBody(_StrictRequest):
    request_id: UUID
    expected_version: StrictInt = Field(gt=0)
    content: DraftContent


class DraftActionBody(_StrictRequest):
    request_id: UUID
    expected_version: StrictInt = Field(gt=0)


class SupplementBody(_StrictRequest):
    request_id: UUID
    content: str = Field(min_length=1, max_length=4000)


def _actor(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication_required", headers=_NO_STORE)
    return context


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    payload: _StrictRequest | None = None,
    query: dict[str, object] | None = None,
) -> JSONResponse:
    context = _actor(request)
    client = getattr(request.app.state, "voc_extension_client", None)
    if client is None:
        raise HTTPException(503, "voc_unavailable", headers=_NO_STORE)
    try:
        upstream = await client.request(
            method,
            path,
            actor_id=context.internal_user_id,
            json=None if payload is None else payload.model_dump(mode="json"),
            query=query,
        )
    except VocUpstreamUnavailable:
        raise HTTPException(503, "voc_unavailable", headers=_NO_STORE) from None
    except VocProtocolError:
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE) from None
    if upstream.status_code not in _ALLOWED_STATUSES:
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE)
    try:
        body = json.loads(upstream.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE) from None
    if body is not None and not isinstance(body, dict):
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE)
    return JSONResponse(body, status_code=upstream.status_code, headers=_NO_STORE)


def build_voc_extension_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/extensions/voc", tags=["voc-extension"])

    @router.post("/drafts")
    async def create_draft(request: Request, payload: CreateDraftBody):
        return await _forward(
            request, "POST", "/api/platform/v1/drafts", payload=payload
        )

    @router.get("/drafts/active")
    async def active_draft(request: Request):
        return await _forward(request, "GET", "/api/platform/v1/drafts/active")

    @router.patch("/drafts/{draft_id}")
    async def update_draft(
        request: Request, draft_id: UUID, payload: UpdateDraftBody
    ):
        return await _forward(
            request,
            "PATCH",
            f"/api/platform/v1/drafts/{draft_id}",
            payload=payload,
        )

    @router.post("/drafts/{draft_id}/cancel")
    async def cancel_draft(
        request: Request, draft_id: UUID, payload: DraftActionBody
    ):
        return await _forward(
            request,
            "POST",
            f"/api/platform/v1/drafts/{draft_id}/cancel",
            payload=payload,
        )

    @router.post("/drafts/{draft_id}/submit")
    async def submit_draft(
        request: Request, draft_id: UUID, payload: DraftActionBody
    ):
        return await _forward(
            request,
            "POST",
            f"/api/platform/v1/drafts/{draft_id}/submit",
            payload=payload,
        )

    @router.get("/vocs")
    async def list_vocs(
        request: Request,
        query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        selected: dict[str, object] = {"limit": limit}
        if query is not None:
            selected["query"] = query
        return await _forward(
            request, "GET", "/api/platform/v1/vocs", query=selected
        )

    @router.get("/vocs/{voc_no}")
    async def get_voc(
        request: Request,
        voc_no: Annotated[str, Path(pattern=_VOC_NO_PATTERN)],
    ):
        return await _forward(
            request, "GET", f"/api/platform/v1/vocs/{voc_no}"
        )

    @router.post("/vocs/{voc_no}/supplements")
    async def supplement_voc(
        request: Request,
        voc_no: Annotated[str, Path(pattern=_VOC_NO_PATTERN)],
        payload: SupplementBody,
    ):
        return await _forward(
            request,
            "POST",
            f"/api/platform/v1/vocs/{voc_no}/supplements",
            payload=payload,
        )

    return router
