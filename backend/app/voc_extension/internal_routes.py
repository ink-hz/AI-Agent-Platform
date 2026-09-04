"""Private Platform projections consumed only by the VOC service."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.control_plane.models import AuthContext
from app.control_plane.voc_access import VocWorkbenchAccessUnavailable

from .directory import VocDirectoryUnavailable
from .internal_identity import (
    VocBotSubject,
    VocBotSubjectResolver,
    VocBrowserSubject,
    VocServiceAuthorizer,
    capabilities_for,
)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_PATH = "/api/v1/internal/voc"
_EMPLOYEE_CAPABILITIES = ("voc.read_self", "voc.submit")


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _BotSubjectBody(_StrictBody):
    staff_id: str = Field(min_length=1, max_length=128)


class _ResolveBody(_StrictBody):
    internal_user_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("internal_user_ids")
    @classmethod
    def canonical_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                if str(UUID(value)) != value:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("internal user ID must be a canonical UUID") from None
        return values


def _service_authorizer(
    bearer: bytes | VocServiceAuthorizer,
) -> VocServiceAuthorizer:
    return (
        bearer
        if isinstance(bearer, VocServiceAuthorizer)
        else VocServiceAuthorizer(bearer)
    )


def _session_subject(auth, request: Request) -> VocBrowserSubject:
    context = getattr(request.state, "auth_context", None)
    csrf_token = getattr(request.state, "csrf_token", None)
    if not isinstance(context, AuthContext) or not isinstance(csrf_token, str):
        raise HTTPException(401, "authentication required", headers=_NO_STORE)
    try:
        snapshot = auth.account_snapshot(context)
        display_name = snapshot.get("display_name") if isinstance(snapshot, dict) else None
    except Exception:  # noqa: BLE001 - external identity read fails closed
        raise HTTPException(503, "identity unavailable", headers=_NO_STORE) from None
    if not isinstance(display_name, str) or not display_name:
        raise HTTPException(503, "identity unavailable", headers=_NO_STORE)
    access = getattr(request.app.state, "voc_access", None)
    if access is None or not callable(getattr(access, "allows", None)):
        raise HTTPException(503, "identity unavailable", headers=_NO_STORE)
    try:
        capabilities = capabilities_for(context, access)
    except VocWorkbenchAccessUnavailable:
        raise HTTPException(503, "identity unavailable", headers=_NO_STORE) from None
    return VocBrowserSubject(
        internal_user_id=context.internal_user_id,
        display_name=display_name,
        read_only=context.hard_stale_read_only,
        capabilities=capabilities,
        csrf_token=csrf_token,
    )


def build_voc_internal_router(
    *,
    auth,
    directory,
    bearer: bytes | VocServiceAuthorizer,
    bot_subject_resolver: VocBotSubjectResolver | None = None,
) -> APIRouter:
    """Build the private-only VOC adapter from startup-owned dependencies."""
    authorizer = _service_authorizer(bearer)
    router = APIRouter(
        tags=["voc-internal"], dependencies=[Depends(authorizer.require)]
    )

    @router.get(f"{_PATH}/browser-subject")
    def browser_subject(request: Request):
        return _session_subject(auth, request).as_json()

    @router.post(f"{_PATH}/bot-subject")
    async def bot_subject(body: _BotSubjectBody):
        if bot_subject_resolver is None:
            raise HTTPException(503, "directory unavailable", headers=_NO_STORE)
        try:
            result = await bot_subject_resolver.resolve(body.staff_id)
        except Exception:  # noqa: BLE001 - injected directory failures are opaque
            raise HTTPException(503, "directory unavailable", headers=_NO_STORE) from None
        if not isinstance(result, VocBotSubject):
            raise HTTPException(404, "not found", headers=_NO_STORE)
        return {
            "internal_user_id": str(result.internal_user_id),
            "active": result.active,
            "capabilities": list(_EMPLOYEE_CAPABILITIES),
        }

    @router.post(f"{_PATH}/submitter-directory/resolve")
    def resolve_submitters(body: _ResolveBody):
        requested = tuple(UUID(value) for value in body.internal_user_ids)
        try:
            names = directory.names_for(frozenset(requested))
        except (VocDirectoryUnavailable, ValueError):
            raise HTTPException(503, "directory unavailable", headers=_NO_STORE) from None
        return [
            {"internal_user_id": str(value), "display_name": names[value]}
            for value in requested
            if value in names
        ]

    @router.get(f"{_PATH}/submitter-directory/options")
    def submitter_options():
        try:
            options: Iterable = directory.list_submitters()
        except VocDirectoryUnavailable:
            raise HTTPException(503, "directory unavailable", headers=_NO_STORE) from None
        return [
            {
                "internal_user_id": str(option.internal_user_id),
                "display_name": option.display_name,
            }
            for option in options
        ]

    return router
