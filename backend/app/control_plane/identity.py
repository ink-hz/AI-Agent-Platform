from __future__ import annotations

import asyncio
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
from .models import DirectoryFreshness


class IdentityResolutionError(RuntimeError):
    """Stable identity resolution failure without provider identifiers."""


class IdentityResolver:
    CORPORATE_SUBJECT_KIND = "employee"
    UNION_SUBJECT_KIND = "employee_union"

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

    def _candidates(
        self, protected: ProtectedProviderId
    ) -> tuple[tuple[int, bytes], ...]:
        provider_id = self.identity_codec.unseal(protected)
        candidates = self.identity_codec.lookup_candidates(
            protected.subject_kind, provider_id
        )
        if not self.identity_codec.matches_lookup(
            subject_kind=protected.subject_kind,
            provider_id=provider_id,
            lookup_hmac=protected.lookup_hmac,
            lookup_key_version=protected.lookup_key_version,
        ):
            raise IdentityResolutionError("provider identity invalid")
        return candidates

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
    def _lookup_rows(cursor, protected, candidates):
        return cursor.execute(
            "select provider_identity_id, internal_user_id, subject_kind, "
            "lookup_hmac, lookup_key_version, encrypted_provider_id, "
            "encryption_key_version from platform_control.provider_identities "
            "where subject_kind=%s and (lookup_key_version,lookup_hmac) in "
            "(select * from unnest(%s::integer[],%s::bytea[]))",
            (
                protected.subject_kind,
                [version for version, _ in candidates],
                [lookup for _, lookup in candidates],
            ),
        ).fetchall()

    def _resolve_transaction(self, member: DingTalkMember) -> UUID:
        corporate = self.identity_codec.seal(
            self.CORPORATE_SUBJECT_KIND,
            self.corporate_provider_id(self._corp_id, member.userid),
        )
        union = self.identity_codec.seal(self.UNION_SUBJECT_KIND, member.unionid)
        corporate_candidates = self._candidates(corporate)
        union_candidates = self._candidates(union)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._check_key_policy(cursor)
                directory_rows = cursor.execute(
                    "select member.generation_id,member.member_key,"
                    "member.internal_user_id,member.subject_kind,member.lookup_hmac,"
                    "member.lookup_key_version,member.encrypted_provider_id,"
                    "member.encryption_key_version from "
                    "platform_control.directory_state state join "
                    "platform_control.directory_generations generation on "
                    "generation.generation_id=state.active_generation_id and "
                    "generation.status='complete' join "
                    "platform_control.directory_members member on "
                    "member.generation_id=generation.generation_id join "
                    "unnest(%s::integer[],%s::bytea[]) candidate(key_version,lookup_hmac) "
                    "on member.lookup_key_version=candidate.key_version and "
                    "member.lookup_hmac=candidate.lookup_hmac where state.singleton "
                    "and member.subject_kind=%s and member.status='active'",
                    (
                        [version for version, _ in corporate_candidates],
                        [lookup for _, lookup in corporate_candidates],
                        corporate.subject_kind,
                    ),
                ).fetchall()
                if len(directory_rows) != 1 or not self.identity_codec.equivalent(
                    self._row_identity(directory_rows[0]), corporate
                ):
                    raise IdentityResolutionError("active directory member unavailable")

                corporate_rows = self._lookup_rows(
                    cursor, corporate, corporate_candidates
                )
                union_rows = self._lookup_rows(cursor, union, union_candidates)
                corporate_user = self._matching_user(corporate_rows, corporate)
                union_user = self._matching_user(union_rows, union)
                mapped = {value for value in (corporate_user, union_user) if value}
                directory_user = directory_rows[0]["internal_user_id"]
                if directory_user is not None:
                    mapped.add(directory_user)
                if len(mapped) > 1:
                    raise IdentityResolutionError("provider identity collision")
                proposed_user_id = next(iter(mapped), uuid4())
                resolved = cursor.execute(
                    "select platform_control.resolve_verified_dingtalk_member("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,%s,%s) as internal_user_id",
                    (
                        proposed_user_id,
                        member.display_name,
                        uuid4(),
                        corporate.lookup_hmac,
                        corporate.lookup_key_version,
                        corporate.ciphertext,
                        corporate.encryption_key_version,
                        [version for version, _ in corporate_candidates],
                        [lookup for _, lookup in corporate_candidates],
                        uuid4(),
                        union.lookup_hmac,
                        union.lookup_key_version,
                        union.ciphertext,
                        union.encryption_key_version,
                        [version for version, _ in union_candidates],
                        [lookup for _, lookup in union_candidates],
                    ),
                ).fetchone()
                if resolved is None:
                    raise IdentityResolutionError("identity persistence unavailable")
                internal_user_id = resolved["internal_user_id"]
                if mapped and internal_user_id not in mapped:
                    raise IdentityResolutionError("provider identity collision")
                return internal_user_id
        except IdentityResolutionError:
            raise
        except (IdentityCryptoError, psycopg.errors.UniqueViolation):
            raise IdentityResolutionError("provider identity collision") from None
        except psycopg.Error:
            raise IdentityResolutionError("identity persistence unavailable") from None

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
