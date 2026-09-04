"""Minimal, immutable identity projections for the private VOC service."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException, Request

from app.control_plane.identity import StaffIdentity
from app.control_plane.models import AuthContext, DirectoryFreshness


@dataclass(frozen=True, slots=True)
class VocBrowserSubject:
    internal_user_id: UUID
    display_name: str
    read_only: bool
    capabilities: tuple[str, ...]
    csrf_token: str

    def as_json(self) -> dict[str, object]:
        return {
            "internal_user_id": str(self.internal_user_id),
            "display_name": self.display_name,
            "read_only": self.read_only,
            "capabilities": list(self.capabilities),
            "csrf_token": self.csrf_token,
        }


@dataclass(frozen=True, slots=True)
class VocBotSubject:
    internal_user_id: UUID
    active: bool


class VocBotSubjectResolver(Protocol):
    async def resolve(self, staff_id: str) -> VocBotSubject | None: ...


class StaffIdentityResolver(Protocol):
    async def resolve_staff_member(
        self,
        userid: str,
        freshness: DirectoryFreshness,
    ) -> StaffIdentity: ...


@dataclass(frozen=True, slots=True)
class PlatformVocBotSubjectResolver:
    """Resolve a DingTalk userid through the startup-owned identity boundary."""

    identity_resolver: StaffIdentityResolver = field(repr=False)
    directory_freshness: Callable[[], DirectoryFreshness] = field(repr=False)

    async def resolve(self, staff_id: str) -> VocBotSubject:
        freshness = self.directory_freshness()
        resolved = await self.identity_resolver.resolve_staff_member(
            staff_id,
            freshness,
        )
        return VocBotSubject(resolved.internal_user_id, resolved.active)


def capabilities_for(context: AuthContext, access) -> tuple[str, ...]:
    values = {"voc.read_self"}
    if not context.hard_stale_read_only:
        values.add("voc.submit")
    if access.allows(context):
        values.add("voc.read_all")
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class VocServiceAuthorizer:
    """One startup-created private-network bearer verifier."""

    expected: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.expected, bytes) or not self.expected:
            raise ValueError("VOC service bearer invalid")

    def require(self, request: Request) -> None:
        require_voc_service(request, self.expected)


def require_voc_service(request: Request, expected: bytes) -> None:
    authorization = request.headers.get("Authorization", "")
    supplied = authorization.removeprefix("Bearer ").encode("utf-8")
    source_ip = request.client.host if request.client else ""
    if source_ip not in {"172.29.0.3", "172.29.0.5"} or not hmac.compare_digest(
        supplied, expected
    ):
        raise HTTPException(404, "not found")
