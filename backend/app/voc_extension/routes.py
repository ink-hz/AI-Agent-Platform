"""Explicit same-origin BFF routes for the native VOC workspace."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
)

from app.control_plane.models import AuthContext, Role

from .client import VocProtocolError, VocUpstreamUnavailable
from .directory import VocDirectoryUnavailable

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ALLOWED_STATUSES = frozenset({200, 201, 401, 403, 404, 409, 422, 503})
_VOC_NO_PATTERN = r"^VOC-[0-9]{8}-[0-9]{3,}$"
_MANAGEMENT_ROLES = frozenset(
    {Role.MANAGEMENT_VIEWER, Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER}
)
_MANAGEMENT_CAPABILITIES = frozenset({"voc.read_all"})


def _reject_nonstandard_json(_value: str):
    raise ValueError("nonstandard JSON constant")


def _decode_json(value: bytes):
    return json.loads(value, parse_constant=_reject_nonstandard_json)


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


class _StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminVocEntry(_StrictResponse):
    revision: int = Field(gt=0)
    entry_type: Literal["original", "supplement", "correction"]
    content: str
    created_at: datetime


class AdminVocSummary(_StrictResponse):
    voc_no: str = Field(pattern=_VOC_NO_PATTERN)
    submitter_internal_user_id: UUID | None
    legacy_submitter_name: str | None
    source: Literal["platform", "dingtalk"]
    latest_content: str
    revision: int = Field(gt=0)
    analysis_status: Literal[
        "pending", "claimed", "succeeded", "failed", "not_requested"
    ]
    created_at: datetime
    updated_at: datetime


class AdminVocPage(_StrictResponse):
    items: tuple[AdminVocSummary, ...]
    next_cursor: str | None


class AdminVocDetail(AdminVocSummary):
    entries: tuple[AdminVocEntry, ...]


def _actor(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication_required", headers=_NO_STORE)
    return context


def _manager(request: Request) -> AuthContext:
    context = _actor(request)
    if context.role not in _MANAGEMENT_ROLES:
        raise HTTPException(403, "forbidden", headers=_NO_STORE)
    return context


async def _request_json(
    request: Request,
    method: str,
    path: str,
    *,
    actor: AuthContext,
    payload: _StrictRequest | None = None,
    query: dict[str, object] | None = None,
    capabilities: frozenset[str] | None = None,
) -> tuple[int, dict[str, object] | None]:
    client = getattr(request.app.state, "voc_extension_client", None)
    if client is None:
        raise HTTPException(503, "voc_unavailable", headers=_NO_STORE)
    arguments: dict[str, object] = {
        "actor_id": actor.internal_user_id,
        "json": None if payload is None else payload.model_dump(mode="json"),
        "query": query,
    }
    if capabilities is not None:
        arguments["capabilities"] = capabilities
    try:
        upstream = await client.request(method, path, **arguments)
    except VocUpstreamUnavailable:
        raise HTTPException(503, "voc_unavailable", headers=_NO_STORE) from None
    except VocProtocolError:
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE) from None
    if upstream.status_code not in _ALLOWED_STATUSES:
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE)
    try:
        body = _decode_json(upstream.body)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE) from None
    if body is not None and not isinstance(body, dict):
        raise HTTPException(502, "voc_protocol_error", headers=_NO_STORE)
    return upstream.status_code, body


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    payload: _StrictRequest | None = None,
    query: dict[str, object] | None = None,
) -> JSONResponse:
    status_code, body = await _request_json(
        request,
        method,
        path,
        actor=_actor(request),
        payload=payload,
        query=query,
    )
    return JSONResponse(body, status_code=status_code, headers=_NO_STORE)


def _submitter_name(
    summary: AdminVocSummary,
    names: dict[UUID, str],
) -> str:
    if summary.submitter_internal_user_id is not None:
        return names.get(
            summary.submitter_internal_user_id,
            f"未知用户 · {str(summary.submitter_internal_user_id)[:8]}",
        )
    return summary.legacy_submitter_name or "历史提交人"


def _enrich_summary(
    summary: AdminVocSummary,
    names: dict[UUID, str],
) -> dict[str, object]:
    result = summary.model_dump(mode="json", exclude={"legacy_submitter_name"})
    result["submitter_name"] = _submitter_name(summary, names)
    return result


def _directory(request: Request):
    directory = getattr(request.app.state, "voc_submitter_directory", None)
    if directory is None:
        raise HTTPException(503, "voc_directory_unavailable", headers=_NO_STORE)
    return directory


def _names_for(request: Request, summaries) -> dict[UUID, str]:
    ids = frozenset(
        item.submitter_internal_user_id
        for item in summaries
        if item.submitter_internal_user_id is not None
    )
    try:
        return _directory(request).names_for(ids)
    except (VocDirectoryUnavailable, ValueError):
        raise HTTPException(
            503, "voc_directory_unavailable", headers=_NO_STORE
        ) from None


def build_voc_extension_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/extensions/voc", tags=["voc-extension"])

    @router.get("/health")
    async def health(request: Request):
        client = getattr(request.app.state, "voc_extension_client", None)
        if client is None:
            raise HTTPException(503, "voc_unavailable", headers=_NO_STORE)
        try:
            upstream = await client.health()
            body = _decode_json(upstream.body)
        except (VocProtocolError, VocUpstreamUnavailable, UnicodeDecodeError, ValueError):
            raise HTTPException(503, "voc_unavailable", headers=_NO_STORE) from None
        if upstream.status_code != 200 or body != {
            "status": "ok",
            "service": "voc-workspace",
        }:
            raise HTTPException(503, "voc_unavailable", headers=_NO_STORE)
        return JSONResponse(body, headers=_NO_STORE)

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

    @router.get("/admin/vocs")
    async def admin_list_vocs(
        request: Request,
        query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        submitter_internal_user_id: UUID | None = None,
        legacy_submitter_name: Annotated[
            str | None, Query(min_length=1, max_length=160)
        ] = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        actor = _manager(request)
        selected: dict[str, object] = {"limit": limit}
        for key, value in (
            ("query", query),
            ("submitter_internal_user_id", submitter_internal_user_id),
            ("legacy_submitter_name", legacy_submitter_name),
            ("created_from", created_from),
            ("created_to", created_to),
            ("cursor", cursor),
        ):
            if value is not None:
                selected[key] = value
        status_code, body = await _request_json(
            request,
            "GET",
            "/api/platform/v1/admin/vocs",
            actor=actor,
            query=selected,
            capabilities=_MANAGEMENT_CAPABILITIES,
        )
        if status_code != 200:
            return JSONResponse(body, status_code=status_code, headers=_NO_STORE)
        try:
            page = AdminVocPage.model_validate(body)
        except ValidationError:
            raise HTTPException(
                502, "voc_protocol_error", headers=_NO_STORE
            ) from None
        names = _names_for(request, page.items)
        return JSONResponse(
            {
                "items": [_enrich_summary(item, names) for item in page.items],
                "next_cursor": page.next_cursor,
            },
            headers=_NO_STORE,
        )

    @router.get("/admin/vocs/{voc_no}")
    async def admin_get_voc(
        request: Request,
        voc_no: Annotated[str, Path(pattern=_VOC_NO_PATTERN)],
    ):
        actor = _manager(request)
        status_code, body = await _request_json(
            request,
            "GET",
            f"/api/platform/v1/admin/vocs/{voc_no}",
            actor=actor,
            capabilities=_MANAGEMENT_CAPABILITIES,
        )
        if status_code != 200:
            return JSONResponse(body, status_code=status_code, headers=_NO_STORE)
        try:
            detail = AdminVocDetail.model_validate(body)
        except ValidationError:
            raise HTTPException(
                502, "voc_protocol_error", headers=_NO_STORE
            ) from None
        names = _names_for(request, (detail,))
        result = _enrich_summary(detail, names)
        result["entries"] = [
            entry.model_dump(mode="json") for entry in detail.entries
        ]
        return JSONResponse(result, headers=_NO_STORE)

    @router.get("/admin/submitters")
    async def admin_list_submitters(request: Request):
        _manager(request)
        try:
            options = _directory(request).list_submitters()
        except VocDirectoryUnavailable:
            raise HTTPException(
                503, "voc_directory_unavailable", headers=_NO_STORE
            ) from None
        return JSONResponse(
            {
                "items": [
                    {
                        "internal_user_id": str(item.internal_user_id),
                        "display_name": item.display_name,
                    }
                    for item in options
                ]
            },
            headers=_NO_STORE,
        )

    return router
