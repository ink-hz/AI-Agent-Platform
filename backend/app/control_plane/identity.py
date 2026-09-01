from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .crypto import IdentityCryptoError, ProtectedProviderId, ProviderIdentityCodec
from .dingtalk import (
    DingTalkAuthResult,
    DingTalkClient,
    DingTalkMember,
    DingTalkProviderError,
)
from .dsn import validate_control_dsn
from .models import (
    ControlUser,
    DirectoryFreshness,
    DirectoryState,
    ResolvedLoginIdentity,
    Role,
    StaleAccessDecision,
)


def decide_stale_access(
    user: ControlUser,
    directory: DirectoryState,
) -> StaleAccessDecision:
    if user.status != "active" or user.locally_invalidated_at is not None:
        return StaleAccessDecision(False, True, "locally_inactive")
    if (
        directory.active_generation_id is None
        or directory.last_complete_at is None
    ):
        return StaleAccessDecision(False, True, "unbound_identity")
    if directory.freshness is DirectoryFreshness.HARD_STALE:
        if user.role in {
            Role.PLATFORM_OWNER,
            Role.PLATFORM_ADMIN,
            Role.MANAGEMENT_VIEWER,
        }:
            if not user.last_confirmed_active:
                return StaleAccessDecision(False, True, "unbound_identity")
            return StaleAccessDecision(
                True,
                True,
                "privileged_last_generation",
            )
        return StaleAccessDecision(False, True, "member_hard_stale")
    if not user.last_confirmed_active:
        return StaleAccessDecision(False, True, "unbound_identity")
    if directory.freshness in {
        DirectoryFreshness.FRESH,
        DirectoryFreshness.WARNING,
    }:
        return StaleAccessDecision(
            True,
            False,
            directory.freshness.value,
        )
    return StaleAccessDecision(False, True, "unbound_identity")


class IdentityResolutionError(RuntimeError):
    """Stable identity resolution failure without provider identifiers."""


@dataclass(frozen=True, slots=True)
class StaffIdentity:
    """Canonical staff identity verified by the configured DingTalk client."""

    internal_user_id: UUID
    active: bool


class IdentityResolver:
    CORPORATE_SUBJECT_KIND = "employee"
    UNION_SUBJECT_KIND = "employee_union"
    DIRECTORY_LOCK_FUNCTION = (
        "platform_control.lock_dingtalk_identity_directory()"
    )

    def __init__(
        self,
        control_database_url: str,
        *,
        corp_id: str,
        client: DingTalkClient,
        identity_codec: ProviderIdentityCodec,
    ) -> None:
        self.environment = validate_control_dsn(
            control_database_url, purpose="app"
        ).environment
        if not isinstance(corp_id, str) or not corp_id.strip() or "\0" in corp_id:
            raise ValueError("DingTalk corp ID invalid")
        if not isinstance(identity_codec, ProviderIdentityCodec):
            raise ValueError("provider identity codec required")
        self._control_database_url = control_database_url
        self._corp_id = corp_id
        self.client = client
        self.identity_codec = identity_codec

    def __repr__(self) -> str:
        return (
            "IdentityResolver(control_database_url=<redacted>, "
            "corp_id=<redacted>, client=<redacted>, identity_codec=<redacted>)"
        )

    @staticmethod
    def corporate_provider_id(corp_id: str, userid: str) -> str:
        if (
            not isinstance(corp_id, str)
            or not corp_id
            or not isinstance(userid, str)
            or not userid
            or "\0" in corp_id
            or "\0" in userid
        ):
            raise IdentityResolutionError("provider identity invalid")
        return f"{len(corp_id.encode('utf-8'))}:{corp_id}{userid}"

    def _connection(self):
        return psycopg.connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def _check_key_policy(self, cursor) -> None:
        configured = tuple(self.identity_codec.hmac.transition_versions or ())
        row = cursor.execute(
            "select lookup_transition_versions from "
            "platform_control.provider_identity_key_policies "
            "where provider='dingtalk'"
        ).fetchone()
        if row is None or tuple(row["lookup_transition_versions"]) != configured:
            raise IdentityResolutionError("identity key policy mismatch")

    @staticmethod
    def _row_identity(row: dict[str, Any]) -> ProtectedProviderId:
        return ProtectedProviderId(
            subject_kind=row["subject_kind"],
            lookup_hmac=bytes(row["lookup_hmac"]),
            lookup_key_version=row["lookup_key_version"],
            ciphertext=bytes(row["encrypted_provider_id"]),
            encryption_key_version=row["encryption_key_version"],
        )

    def _matching_user(
        self,
        rows: list[dict[str, Any]],
        protected: ProtectedProviderId,
    ) -> UUID | None:
        matches: set[UUID] = set()
        for row in rows:
            if not self.identity_codec.equivalent(
                self._row_identity(row), protected
            ):
                raise IdentityResolutionError("provider identity collision")
            matches.add(row["internal_user_id"])
        if len(matches) > 1:
            raise IdentityResolutionError("provider identity collision")
        return next(iter(matches), None)

    @staticmethod
    def _lookup_rows(cursor, protected):
        return cursor.execute(
            "select provider_identity_id, internal_user_id, subject_kind, "
            "lookup_hmac, lookup_key_version, encrypted_provider_id, "
            "encryption_key_version from platform_control.provider_identities "
            "where subject_kind=%s and lookup_key_version=%s "
            "and lookup_hmac=%s",
            (
                protected.subject_kind,
                protected.lookup_key_version,
                protected.lookup_hmac,
            ),
        ).fetchall()

    def _resolve_transaction(self, member: DingTalkMember) -> UUID:
        corporate = self.identity_codec.seal(
            self.CORPORATE_SUBJECT_KIND,
            self.corporate_provider_id(self._corp_id, member.userid),
        )
        union = self.identity_codec.seal(self.UNION_SUBJECT_KIND, member.unionid)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._check_key_policy(cursor)
                cursor.execute(f"select {self.DIRECTORY_LOCK_FUNCTION}")
                directory_rows = cursor.execute(
                    "select * from platform_control."
                    "read_active_directory_member_v20(%s,%s,%s,%s)",
                    (
                        corporate.lookup_key_version,
                        corporate.lookup_hmac,
                        union.lookup_key_version,
                        union.lookup_hmac,
                    ),
                ).fetchall()
                if len(directory_rows) != 1:
                    raise IdentityResolutionError("active directory member unavailable")

                corporate_rows = self._lookup_rows(cursor, corporate)
                union_rows = self._lookup_rows(cursor, union)
                corporate_user = self._matching_user(corporate_rows, corporate)
                union_user = self._matching_user(union_rows, union)
                directory_user = directory_rows[0]["internal_user_id"]
                if (corporate_user is None) != (union_user is None):
                    raise IdentityResolutionError("provider identity collision")
                identity_was_unmapped = corporate_user is None
                if corporate_user is None:
                    if directory_user is not None:
                        raise IdentityResolutionError("provider identity collision")
                    proposed_user_id = uuid4()
                else:
                    if (
                        corporate_user != union_user
                        or (
                            directory_user is not None
                            and directory_user != corporate_user
                        )
                    ):
                        raise IdentityResolutionError("provider identity collision")
                    proposed_user_id = corporate_user
                resolved = cursor.execute(
                    "select platform_control.resolve_verified_dingtalk_member("
                    "%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s) as internal_user_id",
                    (
                        proposed_user_id,
                        member.display_name,
                        uuid4(),
                        corporate.lookup_hmac,
                        corporate.lookup_key_version,
                        corporate.ciphertext,
                        corporate.encryption_key_version,
                        uuid4(),
                        union.lookup_hmac,
                        union.lookup_key_version,
                        union.ciphertext,
                        union.encryption_key_version,
                    ),
                ).fetchone()
                if resolved is None:
                    raise IdentityResolutionError("identity persistence unavailable")
                internal_user_id = resolved["internal_user_id"]
                if not identity_was_unmapped and internal_user_id != proposed_user_id:
                    raise IdentityResolutionError("provider identity collision")
                return internal_user_id
        except IdentityResolutionError:
            raise
        except (IdentityCryptoError, psycopg.errors.UniqueViolation):
            raise IdentityResolutionError("provider identity collision") from None
        except psycopg.Error:
            raise IdentityResolutionError("identity persistence unavailable") from None

    def _resolve_stale_transaction(
        self,
        auth_result: DingTalkAuthResult,
    ) -> ResolvedLoginIdentity:
        union = self.identity_codec.seal(
            self.UNION_SUBJECT_KIND, auth_result.unionid
        )
        corporate = (
            self.identity_codec.seal(
                self.CORPORATE_SUBJECT_KIND,
                self.corporate_provider_id(self._corp_id, auth_result.userid),
            )
            if auth_result.userid is not None
            else None
        )
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._check_key_policy(cursor)
                cursor.execute(f"select {self.DIRECTORY_LOCK_FUNCTION}")
                union_user = self._matching_user(
                    self._lookup_rows(cursor, union), union
                )
                corporate_user = (
                    self._matching_user(
                        self._lookup_rows(cursor, corporate), corporate
                    )
                    if corporate is not None
                    else union_user
                )
                if (
                    union_user is None
                    or corporate_user is None
                    or union_user != corporate_user
                ):
                    raise IdentityResolutionError("directory unavailable")
                status = cursor.execute(
                    "select * from platform_control.read_active_directory_status_v20()"
                ).fetchone()
                user = cursor.execute(
                    "select internal_user_id,role::text,status,"
                    "locally_invalidated_at,last_confirmed_generation_id "
                    "from platform_control.internal_users "
                    "where internal_user_id=%s",
                    (union_user,),
                ).fetchone()
                if status is None or user is None:
                    raise IdentityResolutionError("directory unavailable")
                control_user = ControlUser(
                    internal_user_id=user["internal_user_id"],
                    role=Role(user["role"]),
                    status=user["status"],
                    last_confirmed_active=(
                        user["last_confirmed_generation_id"]
                        == status["active_generation_id"]
                    ),
                    locally_invalidated_at=user["locally_invalidated_at"],
                )
                directory = DirectoryState(
                    active_generation_id=status["active_generation_id"],
                    last_complete_at=status["last_complete_at"],
                    freshness=DirectoryFreshness.HARD_STALE,
                )
                decision = decide_stale_access(control_user, directory)
                if not decision.allowed:
                    raise IdentityResolutionError("directory unavailable")
                return ResolvedLoginIdentity(
                    union_user,
                    decision.read_only,
                    decision.reason,
                )
        except IdentityResolutionError:
            raise
        except (IdentityCryptoError, psycopg.Error, ValueError):
            raise IdentityResolutionError("directory unavailable") from None

    async def resolve_login_identity(
        self,
        auth_result: DingTalkAuthResult,
        freshness: DirectoryFreshness,
    ) -> ResolvedLoginIdentity:
        if not isinstance(auth_result, DingTalkAuthResult):
            raise IdentityResolutionError("authentication result invalid")
        if auth_result.corp_id != self._corp_id:
            raise IdentityResolutionError("organization mismatch")
        if not auth_result.unionid:
            raise IdentityResolutionError("stable identity required")
        if freshness is DirectoryFreshness.HARD_STALE:
            return await asyncio.to_thread(
                self._resolve_stale_transaction, auth_result
            )
        internal_user_id = await self.resolve_active_member(
            auth_result, freshness
        )
        return ResolvedLoginIdentity(
            internal_user_id,
            False,
            freshness.value,
        )

    async def resolve_active_member(
        self,
        auth_result: DingTalkAuthResult,
        freshness: DirectoryFreshness,
    ) -> UUID:
        if not isinstance(auth_result, DingTalkAuthResult):
            raise IdentityResolutionError("authentication result invalid")
        if auth_result.corp_id != self._corp_id:
            raise IdentityResolutionError("organization mismatch")
        if freshness is DirectoryFreshness.HARD_STALE:
            raise IdentityResolutionError("directory unavailable")
        if freshness not in {DirectoryFreshness.FRESH, DirectoryFreshness.WARNING}:
            raise IdentityResolutionError("directory unavailable")
        try:
            if auth_result.userid is None:
                if not auth_result.unionid:
                    raise IdentityResolutionError("stable identity required")
                member = await self.client.resolve_union_member(auth_result.unionid)
            else:
                if not auth_result.unionid:
                    raise IdentityResolutionError("stable identity required")
                member = await self.client.get_member(auth_result.userid)
            if (
                member.userid != auth_result.userid
                and auth_result.userid is not None
            ) or member.unionid != auth_result.unionid:
                raise IdentityResolutionError("provider identity mismatch")
            if not member.active:
                raise IdentityResolutionError("member inactive")
            if not member.display_name.strip():
                raise IdentityResolutionError("member data invalid")
        except IdentityResolutionError:
            raise
        except DingTalkProviderError:
            raise IdentityResolutionError("provider identity unavailable") from None
        return await self._persist_active_member(member)

    def _resolve_inactive_staff_transaction(self, member: DingTalkMember) -> UUID:
        """Read an established inactive identity without changing its state."""
        corporate = self.identity_codec.seal(
            self.CORPORATE_SUBJECT_KIND,
            self.corporate_provider_id(self._corp_id, member.userid),
        )
        union = self.identity_codec.seal(self.UNION_SUBJECT_KIND, member.unionid)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._check_key_policy(cursor)
                cursor.execute(f"select {self.DIRECTORY_LOCK_FUNCTION}")
                directory_rows = cursor.execute(
                    "select * from platform_control."
                    "read_current_inactive_staff_member_v61(%s,%s,%s,%s)",
                    (
                        corporate.lookup_key_version,
                        corporate.lookup_hmac,
                        union.lookup_key_version,
                        union.lookup_hmac,
                    ),
                ).fetchall()
                if (
                    len(directory_rows) != 1
                    or directory_rows[0]["member_status"]
                    not in {"inactive", "disabled"}
                ):
                    raise IdentityResolutionError("directory unavailable")
                corporate_user = self._matching_user(
                    self._lookup_rows(cursor, corporate), corporate
                )
                union_user = self._matching_user(
                    self._lookup_rows(cursor, union), union
                )
                if (
                    corporate_user is None
                    or union_user is None
                    or corporate_user != union_user
                ):
                    raise IdentityResolutionError("directory unavailable")
                user = cursor.execute(
                    "select status,locally_invalidated_at "
                    "from platform_control.internal_users "
                    "where internal_user_id=%s",
                    (corporate_user,),
                ).fetchone()
                if (
                    user is None
                    or user["status"] != "active"
                    or user["locally_invalidated_at"] is not None
                ):
                    raise IdentityResolutionError("directory unavailable")
                return corporate_user
        except IdentityResolutionError:
            raise
        except (IdentityCryptoError, KeyError, psycopg.Error):
            raise IdentityResolutionError("directory unavailable") from None

    async def resolve_staff_member(
        self,
        userid: str,
        freshness: DirectoryFreshness,
    ) -> StaffIdentity:
        """Resolve a verified staff member, persisting only active identities."""
        if freshness not in {DirectoryFreshness.FRESH, DirectoryFreshness.WARNING}:
            raise IdentityResolutionError("directory unavailable")
        if not isinstance(userid, str) or not userid or "\0" in userid:
            raise IdentityResolutionError("provider identity invalid")
        try:
            member = await self.client.get_member(userid)
            if member.userid != userid:
                raise IdentityResolutionError("provider identity mismatch")
            if not member.unionid:
                raise IdentityResolutionError("stable identity required")
            if member.active and not member.display_name.strip():
                raise IdentityResolutionError("member data invalid")
        except IdentityResolutionError:
            raise
        except DingTalkProviderError:
            raise IdentityResolutionError("provider identity unavailable") from None
        if member.active:
            return StaffIdentity(await self._persist_active_member(member), True)
        return StaffIdentity(
            await asyncio.to_thread(self._resolve_inactive_staff_transaction, member),
            False,
        )

    async def _persist_active_member(self, member: DingTalkMember) -> UUID:
        mutation = asyncio.create_task(
            asyncio.to_thread(self._resolve_transaction, member)
        )
        try:
            return await asyncio.shield(mutation)
        except asyncio.CancelledError as cancellation:
            while not mutation.done():
                try:
                    await asyncio.shield(mutation)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if mutation.done() and not mutation.cancelled():
                try:
                    mutation.result()
                except Exception:
                    pass
            raise cancellation
