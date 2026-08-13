from __future__ import annotations

from collections.abc import Callable
import hmac
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
        # Retired by migration 015; WebSessionRepository owns this boundary.
        raise ControlRepositoryError("secure web authentication flow required")

    def consume_login_attempt(self, state_token: str) -> UUID | None:
        # Retired by migration 015; claims precede provider exchange.
        raise ControlRepositoryError("secure web authentication flow required")

    def create_web_session(
        self,
        internal_user_id: UUID,
        idle_seconds: int,
        absolute_seconds: int,
    ) -> IssuedWebSession:
        # Retired: Session issuance requires a claimed login attempt.
        raise ControlRepositoryError("secure web authentication flow required")

    def rotate_web_session(
        self,
        cookie_token: str,
        idle_seconds: int,
    ) -> IssuedWebSession | None:
        # Authentication performs no legacy Session-token rotation.
        raise ControlRepositoryError("secure web authentication flow required")

    def revoke_user_sessions(
        self, internal_user_id: UUID, reason: str
    ) -> int:
        # Bulk revocation is available only through audited/admin functions.
        raise ControlRepositoryError("secure web authentication flow required")

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
