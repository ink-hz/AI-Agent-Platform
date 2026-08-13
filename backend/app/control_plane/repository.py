from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import secrets
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .crypto import (
    IdentityCryptoError,
    ProtectedProviderId,
    ProviderIdentityCodec,
)
from .models import IssuedWebSession


class ControlRepositoryError(RuntimeError):
    """Stable control-database boundary error without protected values."""


class IdentityCollisionError(ControlRepositoryError):
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
    _CONTROL_DATABASES = {
        "agent_platform_control",
        "agent_platform_control_preview",
    }

    def __init__(
        self,
        control_database_url: str,
        *,
        identity_codec: ProviderIdentityCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        try:
            database_name = conninfo_to_dict(control_database_url).get("dbname")
        except (TypeError, ValueError, psycopg.Error):
            raise ValueError("control database DSN required") from None
        if database_name not in self._CONTROL_DATABASES:
            raise ValueError("control database DSN required")
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
        supplied = ((protected.lookup_key_version, protected.lookup_hmac),)
        try:
            provider_id = self.identity_codec.unseal(protected)
        except IdentityCryptoError:
            return supplied
        derived = self.identity_codec.lookup_candidates(
            protected.subject_kind, provider_id
        )
        return tuple(dict.fromkeys(derived + supplied))

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
        if not isinstance(display_name, str) or not display_name.strip():
            raise ControlRepositoryError("display name invalid")
        candidates = self._lookup_candidates(protected)
        lock_key = _advisory_lock_key(candidates[0][1])
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("select pg_advisory_xact_lock(%s)", (lock_key,))
                rows = self._identity_rows(
                    cursor,
                    protected.subject_kind,
                    candidates,
                    for_update=True,
                )
                existing = self._matching_user_id(rows, protected)
                if existing is not None:
                    return existing

                provider_id = self.identity_codec.unseal(protected)
                if not self.identity_codec.matches_lookup(
                    subject_kind=protected.subject_kind,
                    provider_id=provider_id,
                    lookup_hmac=protected.lookup_hmac,
                    lookup_key_version=protected.lookup_key_version,
                ):
                    raise IdentityCollisionError(
                        "provider identity collision"
                    )

                internal_user_id = uuid4()
                cursor.execute(
                    "insert into platform_control.internal_users "
                    "(internal_user_id, display_name, status) "
                    "values (%s, %s, 'active')",
                    (internal_user_id, display_name.strip()),
                )
                cursor.execute(
                    "insert into platform_control.provider_identities "
                    "(provider_identity_id, internal_user_id, subject_kind, "
                    "lookup_hmac, lookup_key_version, encrypted_provider_id, "
                    "encryption_key_version) values (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        uuid4(),
                        internal_user_id,
                        protected.subject_kind,
                        protected.lookup_hmac,
                        protected.lookup_key_version,
                        protected.ciphertext,
                        protected.encryption_key_version,
                    ),
                )
                return internal_user_id
        except (IdentityCollisionError, IdentityCryptoError):
            raise
        except psycopg.errors.UniqueViolation:
            raise IdentityCollisionError("provider identity collision") from None
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

    def rotate_provider_identity(
        self,
        internal_user_id: UUID,
        previous: ProtectedProviderId,
        rotated: ProtectedProviderId,
    ) -> None:
        try:
            rotated_provider_id = self.identity_codec.unseal(rotated)
            if (
                not self.identity_codec.equivalent(previous, rotated)
                or not self.identity_codec.matches_lookup(
                    subject_kind=rotated.subject_kind,
                    provider_id=rotated_provider_id,
                    lookup_hmac=rotated.lookup_hmac,
                    lookup_key_version=rotated.lookup_key_version,
                )
            ):
                raise IdentityCollisionError("provider identity collision")
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select pg_advisory_xact_lock(%s)",
                    (_advisory_lock_key(rotated.lookup_hmac),),
                )
                cursor.execute(
                    "select provider_identity_id, internal_user_id, subject_kind, "
                    "lookup_hmac, lookup_key_version, encrypted_provider_id, "
                    "encryption_key_version from platform_control.provider_identities "
                    "where internal_user_id = %s and subject_kind = %s "
                    "and lookup_hmac = %s and lookup_key_version = %s for update",
                    (
                        internal_user_id,
                        previous.subject_kind,
                        previous.lookup_hmac,
                        previous.lookup_key_version,
                    ),
                )
                row = cursor.fetchone()
                if row is None or not self.identity_codec.equivalent(
                    self._protected_from_row(row), previous
                ):
                    raise IdentityCollisionError("provider identity collision")
                cursor.execute(
                    "select internal_user_id, subject_kind, lookup_hmac, "
                    "lookup_key_version, encrypted_provider_id, encryption_key_version "
                    "from platform_control.provider_identities "
                    "where subject_kind = %s and lookup_hmac = %s "
                    "and lookup_key_version = %s and provider_identity_id <> %s "
                    "for update",
                    (
                        rotated.subject_kind,
                        rotated.lookup_hmac,
                        rotated.lookup_key_version,
                        row["provider_identity_id"],
                    ),
                )
                if cursor.fetchone() is not None:
                    raise IdentityCollisionError("provider identity collision")
                cursor.execute(
                    "update platform_control.provider_identities set "
                    "subject_kind = %s, lookup_hmac = %s, lookup_key_version = %s, "
                    "encrypted_provider_id = %s, encryption_key_version = %s, "
                    "verified_at = now() where provider_identity_id = %s",
                    (
                        rotated.subject_kind,
                        rotated.lookup_hmac,
                        rotated.lookup_key_version,
                        rotated.ciphertext,
                        rotated.encryption_key_version,
                        row["provider_identity_id"],
                    ),
                )
        except (IdentityCollisionError, IdentityCryptoError):
            raise IdentityCollisionError("provider identity collision") from None
        except psycopg.errors.UniqueViolation:
            raise IdentityCollisionError("provider identity collision") from None
        except psycopg.Error:
            raise ControlRepositoryError("control repository unavailable") from None

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
        absolute_seconds: int,
        *,
        existing_absolute_expires_at: datetime | None = None,
    ) -> IssuedWebSession:
        session_id = uuid4()
        cookie_token, csrf_token = ControlRepository._new_session_tokens()
        cursor.execute(
            "with expiry as (select now() as database_now, "
            "least(now() + %s * interval '1 second', "
            "coalesce(%s::timestamptz, 'infinity'::timestamptz)) "
            "as absolute_expires_at) "
            "insert into platform_control.web_sessions "
            "(session_id, internal_user_id, token_hash, csrf_hash, "
            "idle_expires_at, absolute_expires_at) "
            "select %s, %s, %s, %s, "
            "least(database_now + %s * interval '1 second', "
            "absolute_expires_at), absolute_expires_at from expiry "
            "returning idle_expires_at, absolute_expires_at",
            (
                absolute_seconds,
                existing_absolute_expires_at,
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
        absolute_seconds: int,
    ) -> IssuedWebSession | None:
        self._validate_session_lifetimes(idle_seconds, absolute_seconds)
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
                    absolute_seconds,
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
