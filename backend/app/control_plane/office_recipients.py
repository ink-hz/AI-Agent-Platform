from __future__ import annotations

import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Literal, Protocol
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .crypto import (
    EncryptedDirectoryAttribute,
    IdentityCryptoError,
    ProtectedProviderId,
    ProviderIdentityCodec,
)
from .dsn import validate_control_dsn

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_PATH = "/api/v1/internal/office/recipient-directory"


class OfficeRecipientDirectoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfficeDirectoryMember:
    directory_member_id: UUID
    internal_user_id: UUID | None
    display_name: str
    real_name: str | None
    departments: Sequence[str]
    status: Literal["active", "inactive", "disabled"]
    dingtalk_user_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class OfficeDirectoryIssue:
    requested_id: UUID
    reason: Literal["not_found", "inactive", "disabled", "identity_invalid"]


@dataclass(frozen=True)
class OfficeDirectoryPage:
    directory_generation_id: UUID
    members: Sequence[OfficeDirectoryMember]
    next_cursor: UUID | None
    unresolved: Sequence[OfficeDirectoryIssue] = ()


class OfficeRecipientDirectoryRepositoryProtocol(Protocol):
    def search(
        self,
        *,
        query: str,
        department_ids: Sequence[UUID],
        include_descendants: bool,
        limit: int,
        cursor: UUID | None,
    ) -> OfficeDirectoryPage: ...

    def resolve(
        self,
        *,
        directory_member_ids: Sequence[UUID],
        internal_user_ids: Sequence[UUID],
    ) -> OfficeDirectoryPage: ...

    def departments(self) -> Sequence[dict[str, object]]: ...


def corporate_userid(corp_id: str, protected_value: str) -> str:
    prefix, separator, remainder = protected_value.partition(":")
    if not separator or not prefix.isdecimal():
        raise OfficeRecipientDirectoryError("provider_identity_invalid")
    corp_bytes = corp_id.encode("utf-8")
    if int(prefix) != len(corp_bytes) or not remainder.startswith(corp_id):
        raise OfficeRecipientDirectoryError("provider_identity_invalid")
    userid = remainder[len(corp_id):]
    if not userid or len(userid) > 256 or any(ord(ch) < 0x21 for ch in userid):
        raise OfficeRecipientDirectoryError("provider_identity_invalid")
    return userid


class OfficeRecipientDirectoryRepository:
    def __init__(
        self,
        database_url: str,
        *,
        identity_codec: ProviderIdentityCodec,
        corp_id: str,
        connect=psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(database_url, purpose="app")
        if not isinstance(identity_codec, ProviderIdentityCodec):
            raise TypeError("provider identity codec required")
        if not isinstance(corp_id, str) or not corp_id or "\0" in corp_id:
            raise ValueError("DingTalk corp ID invalid")
        self.environment = parsed.environment
        self._database_url = database_url
        self._identity_codec = identity_codec
        self._corp_id = corp_id
        self._connect = connect

    def __repr__(self) -> str:
        return (
            "OfficeRecipientDirectoryRepository(database_url=<redacted>, "
            f"environment={self.environment!r}, corp_id=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def _read(
        self,
        operation: str,
        *,
        query: str = "",
        department_ids: Sequence[UUID] = (),
        include_descendants: bool = False,
        limit: int = 20,
        cursor: UUID | None = None,
        directory_member_ids: Sequence[UUID] = (),
        internal_user_ids: Sequence[UUID] = (),
    ) -> list[dict]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_control."
                    "read_office_recipient_directory_v53("
                    "%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        operation,
                        query,
                        list(department_ids),
                        include_descendants,
                        limit,
                        cursor,
                        list(directory_member_ids),
                        list(internal_user_ids),
                    ),
                ).fetchall()
            return list(rows)
        except psycopg.Error:
            raise OfficeRecipientDirectoryError("directory_unavailable") from None

    @staticmethod
    def _generation(rows: Sequence[dict]) -> UUID:
        generation_id = next(
            (row.get("directory_generation_id") for row in rows if row.get("directory_generation_id")),
            None,
        )
        if not isinstance(generation_id, UUID):
            raise OfficeRecipientDirectoryError("directory_unavailable")
        return generation_id

    def _real_name(self, row: dict, generation_id: UUID, member_id: UUID) -> str | None:
        ciphertext = row.get("real_name_ciphertext")
        if ciphertext is None:
            return None
        try:
            return self._identity_codec.open_attribute(
                EncryptedDirectoryAttribute(
                    purpose="real_name",
                    ciphertext=bytes(ciphertext),
                    nonce=bytes(row["real_name_nonce"]),
                    encryption_key_version=int(row["real_name_encryption_key_version"]),
                ),
                self._corp_id,
                generation_id,
                member_id,
                "real_name",
            )
        except (IdentityCryptoError, KeyError, TypeError, ValueError):
            raise OfficeRecipientDirectoryError("directory_identity_invalid") from None

    @staticmethod
    def _status(row: dict) -> Literal["active", "inactive", "disabled"]:
        status = str(row["status"])
        if status not in {"active", "inactive", "disabled"}:
            raise OfficeRecipientDirectoryError("directory_identity_invalid")
        return status

    def _search_member(
        self, row: dict, generation_id: UUID
    ) -> OfficeDirectoryMember:
        member_id = row["directory_member_id"]
        try:
            return OfficeDirectoryMember(
                directory_member_id=member_id,
                internal_user_id=row.get("internal_user_id"),
                display_name=str(row["display_name"]),
                real_name=self._real_name(row, generation_id, member_id),
                departments=tuple(row.get("departments") or ()),
                status=self._status(row),
            )
        except OfficeRecipientDirectoryError:
            raise
        except (KeyError, TypeError, ValueError):
            raise OfficeRecipientDirectoryError("directory_identity_invalid") from None

    def _resolved_member(self, row: dict) -> OfficeDirectoryMember:
        try:
            plaintext = self._identity_codec.unseal(
                ProtectedProviderId(
                    subject_kind="employee",
                    lookup_hmac=b"\0" * 32,
                    lookup_key_version=1,
                    ciphertext=bytes(row["encrypted_provider_id"]),
                    encryption_key_version=int(row["encryption_key_version"]),
                )
            )
            return OfficeDirectoryMember(
                directory_member_id=row["directory_member_id"],
                internal_user_id=row.get("internal_user_id"),
                display_name="",
                real_name=None,
                departments=(),
                status=self._status(row),
                dingtalk_user_id=corporate_userid(self._corp_id, plaintext),
            )
        except OfficeRecipientDirectoryError:
            raise
        except (IdentityCryptoError, KeyError, TypeError, ValueError):
            raise OfficeRecipientDirectoryError("directory_identity_invalid") from None

    def search(
        self,
        *,
        query: str,
        department_ids: Sequence[UUID],
        include_descendants: bool,
        limit: int,
        cursor: UUID | None,
    ) -> OfficeDirectoryPage:
        rows = self._read(
            "search",
            query=query,
            department_ids=department_ids,
            include_descendants=include_descendants,
            limit=limit,
            cursor=cursor,
        )
        generation_id = self._generation(rows)
        members = tuple(
            self._search_member(row, generation_id)
            for row in rows
            if row.get("row_kind") == "member"
        )
        next_cursor = next(
            (row.get("next_cursor") for row in rows if row.get("next_cursor")),
            None,
        )
        return OfficeDirectoryPage(generation_id, members, next_cursor)

    def resolve(
        self,
        *,
        directory_member_ids: Sequence[UUID],
        internal_user_ids: Sequence[UUID],
    ) -> OfficeDirectoryPage:
        rows = self._read(
            "resolve",
            directory_member_ids=directory_member_ids,
            internal_user_ids=internal_user_ids,
        )
        generation_id = self._generation(rows)
        members: dict[UUID, OfficeDirectoryMember] = {}
        issues: list[OfficeDirectoryIssue] = []
        for row in rows:
            requested_id = row.get("requested_id")
            if row.get("row_kind") == "issue":
                issues.append(OfficeDirectoryIssue(requested_id, row["issue_reason"]))
                continue
            if row.get("row_kind") != "member":
                continue
            try:
                member = self._resolved_member(row)
            except OfficeRecipientDirectoryError:
                issues.append(OfficeDirectoryIssue(requested_id, "identity_invalid"))
                continue
            members[member.directory_member_id] = member
        return OfficeDirectoryPage(
            generation_id,
            tuple(members.values()),
            None,
            tuple(issues),
        )

    def departments(self) -> Sequence[dict[str, object]]:
        rows = self._read("departments")
        self._generation(rows)
        return tuple(
            {
                "department_id": str(row["department_id"]),
                "parent_department_id": (
                    str(row["parent_department_id"])
                    if row.get("parent_department_id") is not None
                    else None
                ),
                "display_name": str(row["department_name"]),
            }
            for row in rows
            if row.get("row_kind") == "department"
        )


class OfficeRecipientDirectoryService:
    def __init__(self, repository: OfficeRecipientDirectoryRepositoryProtocol) -> None:
        self._repository = repository

    def search(
        self,
        *,
        query: str,
        department_ids: Sequence[UUID],
        include_descendants: bool,
        limit: int,
        cursor: UUID | None,
    ) -> OfficeDirectoryPage:
        return self._repository.search(
            query=query,
            department_ids=department_ids,
            include_descendants=include_descendants,
            limit=limit,
            cursor=cursor,
        )

    def resolve(
        self,
        *,
        directory_member_ids: Sequence[UUID],
        internal_user_ids: Sequence[UUID],
    ) -> OfficeDirectoryPage:
        return self._repository.resolve(
            directory_member_ids=directory_member_ids,
            internal_user_ids=internal_user_ids,
        )

    def departments(self) -> Sequence[dict[str, object]]:
        return self._repository.departments()


class _NoStoreRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                response = await original(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=422,
                    detail=error.errors(),
                    headers=_NO_STORE,
                ) from None
            response.headers.update(_NO_STORE)
            return response

        return handler


class _SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=256)
    department_ids: list[UUID] = Field(default_factory=list, max_length=200)
    include_descendants: bool = False
    limit: int = Field(default=20, ge=1, le=200)
    cursor: UUID | None = None


class _ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory_member_ids: list[UUID] = Field(default_factory=list, max_length=200)
    internal_user_ids: list[UUID] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def bounded_request(self):
        if not self.directory_member_ids and not self.internal_user_ids:
            raise ValueError("at least one recipient identifier is required")
        if len(self.directory_member_ids) + len(self.internal_user_ids) > 200:
            raise ValueError("recipient identifier limit exceeded")
        return self


def _loopback(request: Request) -> bool:
    edge_source = getattr(request.state, "edge_source", None)
    if edge_source is not None:
        return bool(edge_source.ip.is_loopback)
    if request.client is None:
        return False
    try:
        return ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def build_office_recipient_router(
    service: OfficeRecipientDirectoryService,
    *,
    bearer_secret: str,
) -> APIRouter:
    if not isinstance(bearer_secret, str) or len(bearer_secret.encode("utf-8")) < 32:
        raise ValueError("office recipient bearer must contain at least 32 bytes")
    expected_authorization = f"Bearer {bearer_secret}"

    def authorize(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not _loopback(request) or not hmac.compare_digest(
            supplied, expected_authorization
        ):
            raise HTTPException(404, "not found", headers=_NO_STORE)

    router = APIRouter(
        tags=["office-recipient-directory"],
        route_class=_NoStoreRoute,
        dependencies=[Depends(authorize)],
    )

    @router.post(f"{_PATH}/search")
    def search(body: _SearchBody, request: Request):
        try:
            page = service.search(
                query=body.query,
                department_ids=body.department_ids,
                include_descendants=body.include_descendants,
                limit=body.limit,
                cursor=body.cursor,
            )
        except OfficeRecipientDirectoryError:
            raise HTTPException(503, "directory unavailable", headers=_NO_STORE) from None
        return {
            "directory_generation_id": str(page.directory_generation_id),
            "members": [
                {
                    "directory_member_id": str(member.directory_member_id),
                    "internal_user_id": (
                        str(member.internal_user_id)
                        if member.internal_user_id is not None
                        else None
                    ),
                    "display_name": member.display_name,
                    "real_name": member.real_name,
                    "departments": list(member.departments),
                    "status": member.status,
                }
                for member in page.members
            ],
            "next_cursor": str(page.next_cursor) if page.next_cursor else None,
        }

    @router.post(f"{_PATH}/resolve")
    def resolve(body: _ResolveBody, request: Request):
        try:
            page = service.resolve(
                directory_member_ids=body.directory_member_ids,
                internal_user_ids=body.internal_user_ids,
            )
        except OfficeRecipientDirectoryError:
            raise HTTPException(503, "directory unavailable", headers=_NO_STORE) from None
        return {
            "directory_generation_id": str(page.directory_generation_id),
            "members": [
                {
                    "directory_member_id": str(member.directory_member_id),
                    "internal_user_id": (
                        str(member.internal_user_id)
                        if member.internal_user_id is not None
                        else None
                    ),
                    "dingtalk_user_id": member.dingtalk_user_id,
                    "status": member.status,
                }
                for member in page.members
            ],
            "unresolved": [
                {"requested_id": str(issue.requested_id), "reason": issue.reason}
                for issue in page.unresolved
            ],
        }

    @router.get(f"{_PATH}/departments")
    def departments(request: Request):
        try:
            values = service.departments()
        except OfficeRecipientDirectoryError:
            raise HTTPException(503, "directory unavailable", headers=_NO_STORE) from None
        return {"departments": list(values)}

    return router
