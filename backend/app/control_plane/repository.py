from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import hmac
import secrets
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .crypto import (
    IdentityCryptoError,
    ProtectedProviderId,
    ProviderIdentityCodec,
)
from .models import IssuedWebSession
from .dsn import validate_control_dsn


class ControlRepositoryError(RuntimeError):
    """Stable control-database boundary error without protected values."""


class IdentityCollisionError(ControlRepositoryError):
    pass


class IdentityKeyPolicyError(ControlRepositoryError):
    pass


class LoginAttemptCollisionError(ControlRepositoryError):
    pass


def _token_hash(token: str) -> bytes:
    try:
        if not isinstance(token, str) or not token:
            raise ValueError
        return hashlib.sha256(token.encode("utf-8")).digest()
    except (TypeError, ValueError, UnicodeError):
        raise ControlRepositoryError("opaque token invalid") from None


def _advisory_lock_key(value: bytes) -> int:
    unsigned = int.from_bytes(value[:8], "big", signed=False)
    return unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned


class ControlRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        identity_codec: ProviderIdentityCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(identity_codec, ProviderIdentityCodec):
            raise ValueError("provider identity codec required")
        self._control_database_url = control_database_url
        self._connect = connect
        self.identity_codec = identity_codec

    def __repr__(self) -> str:
        return (
            "ControlRepository(control_database_url=<redacted>, "
            "identity_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    @staticmethod
    def _protected_from_row(row: dict[str, Any]) -> ProtectedProviderId:
        return ProtectedProviderId(
            subject_kind=row["subject_kind"],
            lookup_hmac=bytes(row["lookup_hmac"]),
            lookup_key_version=row["lookup_key_version"],
            ciphertext=bytes(row["encrypted_provider_id"]),
            encryption_key_version=row["encryption_key_version"],
        )

    def _lookup_candidates(
        self, protected: ProtectedProviderId
    ) -> tuple[tuple[int, bytes], ...]:
        try:
            provider_id = self.identity_codec.unseal(protected)
            derived = self.identity_codec.lookup_candidates(
                protected.subject_kind, provider_id
            )
            if not any(
                protected.lookup_key_version == version
                and hmac.compare_digest(protected.lookup_hmac, lookup_hmac)
                for version, lookup_hmac in derived
            ):
                raise IdentityCollisionError("provider identity collision")
            return derived
        except IdentityCollisionError:
            raise
        except (AttributeError, IdentityCryptoError, TypeError, ValueError):
            raise IdentityCollisionError("provider identity collision") from None

    def _ensure_identity_key_policy(self, cursor) -> None:
        configured = tuple(self.identity_codec.hmac.transition_versions or ())
        cursor.execute("select pg_advisory_xact_lock(%s)", (1229998928,))
        cursor.execute(
            "insert into platform_control.provider_identity_key_policies "
            "(provider, lookup_transition_versions) values "
            "('dingtalk', %s) on conflict (provider) do nothing",
            (list(configured),),
        )
        cursor.execute(
            "select lookup_transition_versions from "
            "platform_control.provider_identity_key_policies "
            "where provider = 'dingtalk'"
        )
        row = cursor.fetchone()
        if row is None or tuple(row["lookup_transition_versions"]) != configured:
            raise IdentityKeyPolicyError("provider identity key policy mismatch")

    @staticmethod
    def _identity_rows(cursor, subject_kind, candidates, *, for_update):
        versions = [version for version, _ in candidates]
        lookups = [lookup for _, lookup in candidates]
        locking = " for update" if for_update else ""
        return cursor.execute(
            "select identity.provider_identity_id, identity.internal_user_id, "
            "identity.subject_kind, identity.lookup_hmac, "
            "identity.lookup_key_version, identity.encrypted_provider_id, "
            "identity.encryption_key_version "
            "from platform_control.provider_identities identity "
            "join unnest(%s::integer[], %s::bytea[]) "
            "as candidate(key_version, lookup_value) "
            "on identity.lookup_key_version = candidate.key_version "
            "and identity.lookup_hmac = candidate.lookup_value "
            "where identity.subject_kind = %s" + locking,
            (versions, lookups, subject_kind),
        ).fetchall()

    def _matching_user_id(
        self,
        rows: list[dict[str, Any]],
        protected: ProtectedProviderId,
    ) -> UUID | None:
        matching: set[UUID] = set()
        for row in rows:
            try:
                equivalent = self.identity_codec.equivalent(
                    self._protected_from_row(row), protected
                )
            except IdentityCryptoError:
                raise IdentityCollisionError(
                    "provider identity collision"
                ) from None
            if not equivalent:
                raise IdentityCollisionError(
                    "provider identity collision"
                )
            matching.add(row["internal_user_id"])
        if len(matching) > 1:
            raise IdentityCollisionError("provider identity collision")
        return next(iter(matching), None)

    def resolve_provider_identity(
        self, protected: ProtectedProviderId
    ) -> UUID | None:
        candidates = self._lookup_candidates(protected)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                self._ensure_identity_key_policy(cursor)
                rows = self._identity_rows(
                    cursor,
                    protected.subject_kind,
                    candidates,
                    for_update=False,
                )
            return self._matching_user_id(rows, protected)
        except (IdentityCollisionError, IdentityCryptoError):
            raise
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

    def create_internal_user(
        self, protected: ProtectedProviderId, display_name: str
    ) -> UUID:
        raise ControlRepositoryError(
            "verified directory identity required"
        )

    def rotate_provider_identity(
        self,
        internal_user_id: UUID,
        previous: ProtectedProviderId,
        rotated: ProtectedProviderId,
    ) -> None:
        raise ControlRepositoryError(
            "verified directory identity required"
        )

    def create_login_attempt(
        self,
        attempt_kind: str,
        state_token: str,
        ttl_seconds: int,
        *,
        return_path: str | None = None,
        challenge_token: str | None = None,
    ) -> UUID:
        if attempt_kind not in {"qr", "in_client"}:
            raise ControlRepositoryError("login attempt invalid")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or ttl_seconds <= 0
        ):
            raise ControlRepositoryError("login attempt invalid")
        state_hash = _token_hash(state_token)
        challenge_hash = (
            _token_hash(challenge_token) if challenge_token is not None else None
        )
        login_attempt_id = uuid4()
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select pg_advisory_xact_lock(%s)",
                    (_advisory_lock_key(state_hash),),
                )
                cursor.execute(
                    "select login_attempt_id from platform_control.login_attempts "
                    "where state_hash = %s for update",
                    (state_hash,),
                )
                if cursor.fetchone() is not None:
                    raise LoginAttemptCollisionError("login attempt collision")
                cursor.execute(
                    "insert into platform_control.login_attempts "
                    "(login_attempt_id, attempt_kind, state_hash, challenge_hash, "
                    "return_path, expires_at) values "
                    "(%s, %s, %s, %s, %s, now() + %s * interval '1 second')",
                    (
                        login_attempt_id,
                        attempt_kind,
                        state_hash,
                        challenge_hash,
                        return_path,
                        ttl_seconds,
                    ),
                )
            return login_attempt_id
        except LoginAttemptCollisionError:
            raise
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

    def consume_login_attempt(self, state_token: str) -> UUID | None:
        state_hash = _token_hash(state_token)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select login_attempt_id from platform_control.login_attempts "
                    "where state_hash = %s and consumed_at is null "
                    "and expires_at > now() for update",
                    (state_hash,),
                )
                rows = cursor.fetchall()
                if len(rows) != 1:
                    return None
                attempt_id = rows[0]["login_attempt_id"]
                cursor.execute(
                    "update platform_control.login_attempts set consumed_at = now() "
                    "where login_attempt_id = %s",
                    (attempt_id,),
                )
                return attempt_id
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

    @staticmethod
    def _validate_session_lifetimes(
        idle_seconds: int, absolute_seconds: int
    ) -> None:
        if (
            isinstance(idle_seconds, bool)
            or isinstance(absolute_seconds, bool)
            or not isinstance(idle_seconds, int)
            or not isinstance(absolute_seconds, int)
            or idle_seconds <= 0
            or absolute_seconds <= 0
            or idle_seconds > absolute_seconds
        ):
            raise ControlRepositoryError("session lifetime invalid")

    @staticmethod
    def _new_session_tokens() -> tuple[str, str]:
        return secrets.token_urlsafe(32), secrets.token_urlsafe(32)

    @staticmethod
    def _insert_session(
        cursor,
        internal_user_id: UUID,
        idle_seconds: int,
        absolute_seconds: int | None,
        *,
        existing_absolute_expires_at: datetime | None = None,
    ) -> IssuedWebSession:
        session_id = uuid4()
        cookie_token, csrf_token = ControlRepository._new_session_tokens()
        cursor.execute(
            "with expiry as (select now() as database_now, "
            "coalesce(%s::timestamptz, "
            "now() + %s * interval '1 second') as absolute_expires_at) "
            "insert into platform_control.web_sessions "
            "(session_id, internal_user_id, token_hash, csrf_hash, "
            "idle_expires_at, absolute_expires_at) "
            "select %s, %s, %s, %s, "
            "least(database_now + %s * interval '1 second', "
            "absolute_expires_at), absolute_expires_at from expiry "
            "returning idle_expires_at, absolute_expires_at",
            (
                existing_absolute_expires_at,
                absolute_seconds,
                session_id,
                internal_user_id,
                _token_hash(cookie_token),
                _token_hash(csrf_token),
                idle_seconds,
            ),
        )
        row = cursor.fetchone()
        return IssuedWebSession(
            session_id=session_id,
            cookie_token=cookie_token,
            csrf_token=csrf_token,
            idle_expires_at=row["idle_expires_at"],
            absolute_expires_at=row["absolute_expires_at"],
        )

    def create_web_session(
        self,
        internal_user_id: UUID,
        idle_seconds: int,
        absolute_seconds: int,
    ) -> IssuedWebSession:
        self._validate_session_lifetimes(idle_seconds, absolute_seconds)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                return self._insert_session(
                    cursor,
                    internal_user_id,
                    idle_seconds,
                    absolute_seconds,
                )
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

    def rotate_web_session(
        self,
        cookie_token: str,
        idle_seconds: int,
    ) -> IssuedWebSession | None:
        if (
            isinstance(idle_seconds, bool)
            or not isinstance(idle_seconds, int)
            or idle_seconds <= 0
        ):
            raise ControlRepositoryError("session lifetime invalid")
        token_hash = _token_hash(cookie_token)
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select session_id, internal_user_id, absolute_expires_at "
                    "from platform_control.web_sessions where token_hash = %s "
                    "and revoked_at is null and idle_expires_at > now() "
                    "and absolute_expires_at > now() for update",
                    (token_hash,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                cursor.execute(
                    "update platform_control.web_sessions set revoked_at = now(), "
                    "revoked_reason = 'rotated' where session_id = %s",
                    (row["session_id"],),
                )
                return self._insert_session(
                    cursor,
                    row["internal_user_id"],
                    idle_seconds,
                    None,
                    existing_absolute_expires_at=row["absolute_expires_at"],
                )
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

    def revoke_user_sessions(
        self, internal_user_id: UUID, reason: str
    ) -> int:
        if not isinstance(reason, str) or not reason.strip():
            raise ControlRepositoryError("session revocation reason invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "update platform_control.web_sessions set revoked_at = now(), "
                    "revoked_reason = %s where internal_user_id = %s "
                    "and revoked_at is null",
                    (reason.strip(), internal_user_id),
                )
                return cursor.rowcount
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

    def list_observation_scopes(
        self, internal_user_id: UUID
    ) -> tuple[str, ...]:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select agent_id from platform_control.observation_grants "
                    "where viewer_internal_user_id = %s and revoked_at is null "
                    "order by agent_id",
                    (internal_user_id,),
                )
                return tuple(row["agent_id"] for row in cursor.fetchall())
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None
