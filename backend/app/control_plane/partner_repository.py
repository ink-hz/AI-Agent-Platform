from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.execution_relay.content_crypto import SealedContent

from .dsn import validate_control_dsn
from .partner_identity_crypto import (
    PartnerProviderIdentityCodec,
    PartnerProviderIdentityCryptoError,
    ProtectedPartnerProviderIdentity,
)
from .partner_models import (
    PartnerBindingRequest,
    PartnerIdentityResolution,
    PartnerOperator,
    PartnerOrganization,
    PartnerStatus,
)


class PartnerRepositoryError(RuntimeError):
    """Stable partner repository error without protected values."""

    def __init__(self, code: str, status_code: int = 503) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class PartnerRepository:
    def __init__(
        self,
        control_database_url: str,
        *,
        identity_codec: PartnerProviderIdentityCodec,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        if not isinstance(identity_codec, PartnerProviderIdentityCodec):
            raise TypeError("partner provider identity codec required")
        self.environment = parsed.environment
        self._control_database_url = control_database_url
        self._connect = connect
        self.identity_codec = identity_codec

    def __repr__(self) -> str:
        return (
            "PartnerRepository(control_database_url=<redacted>, "
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
    def _organization(row: dict[str, Any]) -> PartnerOrganization:
        return PartnerOrganization(
            partner_organization_id=row["partner_organization_id"],
            status=PartnerStatus(row["status"]),
        )

    @staticmethod
    def _operator(row: dict[str, Any]) -> PartnerOperator:
        return PartnerOperator(
            partner_operator_id=row["partner_operator_id"],
            subject_id=row["subject_id"],
            partner_organization_id=row["partner_organization_id"],
            status=PartnerStatus(row["status"]),
        )

    @staticmethod
    def _database_code(error: psycopg.Error, *, mutation: bool) -> tuple[str, int]:
        message = getattr(error.diag, "message_primary", "") or ""
        stable = {
            **{code: (code, 401) for code in ("partner_auth_state_invalid", "partner_auth_state_replay", "partner_auth_state_expired")},
            **{code: (code, 403) for code in ("provider_identity_inactive", "subject_inactive", "fae_access_denied")},
            "required_audit_unavailable": ("required_audit_unavailable", 503),
            "organization_inactive": ("organization_inactive", 409),
            "operator_inactive": ("operator_inactive", 409),
            "binding_request_unavailable": ("binding_request_unavailable", 409),
            "partner_identity_conflict": ("partner_identity_conflict", 409),
            "partner_identity_already_linked": (
                "partner_identity_already_linked",
                409,
            ),
            "partner owner mutation caller invalid": ("owner_required", 403),
            "active platform owner required": ("owner_required", 403),
        }
        for marker, result in stable.items():
            if marker in message:
                return result
        if isinstance(error, psycopg.errors.UniqueViolation):
            return "partner_identity_conflict", 409
        if isinstance(error, psycopg.errors.InsufficientPrivilege):
            return "owner_required", 403
        if mutation:
            return "partner_identity_unavailable", 503
        return "partner_identity_unavailable", 503

    @classmethod
    def _raise_database_error(cls, error: psycopg.Error, *, mutation: bool) -> None:
        code, status_code = cls._database_code(error, mutation=mutation)
        raise PartnerRepositoryError(code, status_code) from None

    def list_organizations(self) -> tuple[dict[str, Any], ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select partner_organization_id,status,name_ciphertext,"
                    "name_key_version,created_at,updated_at,invalidated_at "
                    "from platform_control.partner_organizations "
                    "order by created_at,partner_organization_id"
                ).fetchall()
            return tuple(dict(row) for row in rows)
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)

    def list_operators(self) -> tuple[dict[str, Any], ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select operator.partner_operator_id,operator.subject_id,"
                    "operator.partner_organization_id,operator.status,"
                    "subject.display_name_ciphertext,"
                    "subject.display_name_key_version,operator.created_at,"
                    "operator.updated_at,operator.invalidated_at,"
                    "grant_row.created_at as fae_granted_at "
                    "from platform_control.partner_operators operator "
                    "join platform_control.agent_access_subjects subject "
                    "on subject.subject_id=operator.subject_id "
                    "left join lateral (select grant_item.created_at from "
                    "platform_control.partner_agent_grants grant_item where "
                    "grant_item.subject_id=operator.subject_id and "
                    "grant_item.agent_id='ai-fae-agent' and "
                    "grant_item.revoked_at is null order by "
                    "grant_item.created_at desc limit 1) grant_row on true "
                    "order by operator.created_at,operator.partner_operator_id"
                ).fetchall()
            return tuple(dict(row) for row in rows)
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)

    def list_binding_requests(self) -> tuple[dict[str, Any], ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select binding_request_id,provider_kind,"
                    "display_name_ciphertext,display_name_key_version,status,"
                    "verified_at,requested_at,expires_at,resolved_at,"
                    "linked_partner_operator_id from "
                    "platform_control.partner_identity_binding_requests "
                    "order by requested_at desc,binding_request_id desc"
                ).fetchall()
            return tuple(dict(row) for row in rows)
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)

    def create_login_attempt(
        self,
        *,
        provider_kind: str,
        state_digest: bytes,
        state_key_version: int,
    ) -> UUID:
        login_attempt_id = uuid4()
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.create_partner_login_attempt_v56("
                    "%s,%s,%s,%s) as login_attempt_id",
                    (
                        login_attempt_id,
                        provider_kind,
                        state_digest,
                        state_key_version,
                    ),
                ).fetchone()
            if row is None or row["login_attempt_id"] != login_attempt_id:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return login_attempt_id
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def consume_login_attempt(
        self,
        *,
        provider_kind: str,
        state_digest: bytes,
        state_key_version: int,
    ) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.consume_partner_login_attempt_v56("
                    "%s,%s,%s) as status",
                    (provider_kind, state_digest, state_key_version),
                ).fetchone()
            status = row["status"] if row is not None else None
            if status == "consumed":
                return
            errors = {
                "invalid": "partner_auth_state_invalid",
                "replay": "partner_auth_state_replay",
                "expired": "partner_auth_state_expired",
            }
            code = errors.get(status)
            if code is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
            raise PartnerRepositoryError(code, 401)
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def create_organization(
        self,
        *,
        partner_organization_id: UUID,
        actor_id: UUID,
        display_name_ciphertext: bytes,
        display_name_key_version: int,
        reason: str,
        request_id: UUID,
    ) -> PartnerOrganization:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.create_partner_organization_v54("
                    "%s,%s,%s,%s,%s,%s,%s) as partner_organization_id",
                    (
                        partner_organization_id,
                        actor_id,
                        display_name_ciphertext,
                        display_name_key_version,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None or row["partner_organization_id"] != partner_organization_id:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return PartnerOrganization(partner_organization_id, PartnerStatus.ACTIVE)
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def create_operator(
        self,
        *,
        partner_operator_id: UUID,
        subject_id: UUID,
        partner_organization_id: UUID,
        actor_id: UUID,
        display_name_ciphertext: bytes,
        display_name_key_version: int,
        reason: str,
        request_id: UUID,
    ) -> PartnerOperator:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.create_partner_operator_v54("
                    "%s,%s,%s,%s,%s,%s,%s,'active',%s,%s)",
                    (
                        partner_operator_id,
                        subject_id,
                        partner_organization_id,
                        actor_id,
                        display_name_ciphertext,
                        display_name_key_version,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return self._operator(row)
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def set_organization_status(
        self,
        *,
        actor_id: UUID,
        partner_organization_id: UUID,
        status: str,
        reason: str,
        request_id: UUID,
    ) -> PartnerOrganization:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control."
                    "set_partner_organization_status_v54(%s,%s,%s,%s,%s,%s)",
                    (
                        actor_id,
                        partner_organization_id,
                        status,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return self._organization(row)
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def set_operator_status(
        self,
        *,
        actor_id: UUID,
        partner_operator_id: UUID,
        status: str,
        reason: str,
        request_id: UUID,
    ) -> PartnerOperator:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control."
                    "set_partner_operator_status_v54(%s,%s,%s,%s,%s,%s)",
                    (
                        actor_id,
                        partner_operator_id,
                        status,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return self._operator(row)
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def grant_fae(
        self,
        *,
        actor_id: UUID,
        partner_operator_id: UUID,
        reason: str,
        request_id: UUID,
    ) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.grant_partner_fae_v54("
                    "%s,%s,%s,%s,%s,%s) as grant_id",
                    (
                        uuid4(),
                        actor_id,
                        partner_operator_id,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None or row["grant_id"] is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def revoke_fae(
        self,
        *,
        actor_id: UUID,
        partner_operator_id: UUID,
        reason: str,
        request_id: UUID,
    ) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.revoke_partner_fae_v54("
                    "%s,%s,%s,%s,%s) as grant_id",
                    (
                        actor_id,
                        partner_operator_id,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None or row["grant_id"] is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def decide_fae_access(self, subject_id: UUID) -> str:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.decide_partner_fae_access_v54(%s) "
                    "as reason",
                    (subject_id,),
                ).fetchone()
            if row is None or row["reason"] not in {
                "active",
                "subject_inactive",
                "organization_inactive",
                "operator_inactive",
                "fae_access_denied",
            }:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return row["reason"]
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)

    def get_fae_subject_identity(
        self, subject_id: UUID, provider_kind: str
    ) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_control."
                    "get_partner_fae_subject_v57(%s,%s)",
                    (subject_id, provider_kind),
                ).fetchall()
            if len(rows) != 1:
                raise PartnerRepositoryError("provider_identity_inactive", 403)
            return dict(rows[0])
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)

    @staticmethod
    def _protected_from_row(row: dict[str, Any]) -> ProtectedPartnerProviderIdentity:
        return ProtectedPartnerProviderIdentity(
            provider_kind=row["provider_kind"],
            provider_subject_lookup_hmac=bytes(row["provider_subject_lookup_hmac"]),
            lookup_key_version=row["lookup_key_version"],
            provider_subject_ciphertext=bytes(row["provider_subject_ciphertext"]),
            encryption_key_version=row["encryption_key_version"],
        )

    def _lookup_candidates(
        self, protected: ProtectedPartnerProviderIdentity
    ) -> tuple[tuple[int, bytes], ...]:
        try:
            provider_subject = self.identity_codec.unseal(protected)
            candidates = self.identity_codec.lookup_candidates(
                protected.provider_kind, provider_subject
            )
            if not any(
                version == protected.lookup_key_version
                and hmac.compare_digest(lookup, protected.lookup_hmac)
                for version, lookup in candidates
            ):
                raise PartnerRepositoryError("partner_identity_conflict", 409)
            return candidates
        except PartnerRepositoryError:
            raise
        except PartnerProviderIdentityCryptoError:
            raise PartnerRepositoryError("partner_identity_conflict", 409) from None

    def _ensure_identity_key_policy(self, connection) -> None:
        configured = list(self.identity_codec.hmac_keyring.transition_versions or ())
        connection.execute(
            "select platform_control.require_partner_identity_key_policy_v54(%s)",
            (configured,),
        )

    def resolve_provider_identity(
        self, protected: ProtectedPartnerProviderIdentity
    ) -> PartnerIdentityResolution | None:
        try:
            candidates = self._lookup_candidates(protected)
            versions = [version for version, _lookup in candidates]
            lookups = [lookup for _version, lookup in candidates]
            with self._connection() as connection:
                self._ensure_identity_key_policy(connection)
                rows = connection.execute(
                    "select identity.provider_kind,"
                    "identity.provider_subject_lookup_hmac,"
                    "identity.lookup_key_version,"
                    "identity.provider_subject_ciphertext,"
                    "identity.encryption_key_version,identity.revoked_at,"
                    "operator.subject_id,operator.partner_operator_id,"
                    "operator.partner_organization_id "
                    "from platform_control.partner_provider_identities identity "
                    "join platform_control.partner_operators operator using "
                    "(partner_operator_id) join unnest(%s::integer[],%s::bytea[]) "
                    "candidate(key_version,lookup_value) on "
                    "identity.lookup_key_version=candidate.key_version and "
                    "identity.provider_subject_lookup_hmac=candidate.lookup_value "
                    "where identity.provider_kind=%s",
                    (versions, lookups, protected.provider_kind),
                ).fetchall()
            resolutions: set[tuple[UUID, UUID, UUID, str]] = set()
            for row in rows:
                if not self.identity_codec.equivalent(
                    self._protected_from_row(row), protected
                ):
                    raise PartnerRepositoryError("partner_identity_conflict", 409)
                resolutions.add(
                    (
                        row["subject_id"],
                        row["partner_operator_id"],
                        row["partner_organization_id"],
                        "revoked" if row["revoked_at"] is not None else "linked",
                    )
                )
            if not resolutions:
                return None
            if len(resolutions) != 1:
                raise PartnerRepositoryError("partner_identity_conflict", 409)
            subject_id, operator_id, organization_id, status = next(iter(resolutions))
            return PartnerIdentityResolution(
                subject_id=subject_id,
                partner_operator_id=operator_id,
                partner_organization_id=organization_id,
                binding_request_id=None,
                status=status,
            )
        except PartnerRepositoryError:
            raise
        except PartnerProviderIdentityCryptoError:
            raise PartnerRepositoryError("partner_identity_conflict", 409) from None
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)

    def record_binding_request(
        self,
        *,
        binding_request_id: UUID,
        protected_identity: ProtectedPartnerProviderIdentity,
        display_name_ciphertext: bytes | None,
        display_name_key_version: int | None,
        verified_at: datetime,
    ) -> PartnerBindingRequest:
        try:
            candidates = self._lookup_candidates(protected_identity)
            versions = [version for version, _lookup in candidates]
            lookups = [lookup for _version, lookup in candidates]
            with self._connection() as connection:
                self._ensure_identity_key_policy(connection)
                row = connection.execute(
                    "select * from platform_control."
                    "record_partner_binding_request_v54("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        binding_request_id,
                        protected_identity.provider_kind,
                        protected_identity.lookup_hmac,
                        protected_identity.lookup_key_version,
                        versions,
                        lookups,
                        protected_identity.ciphertext,
                        protected_identity.encryption_key_version,
                        display_name_ciphertext,
                        display_name_key_version,
                        verified_at,
                    ),
                ).fetchone()
            if row is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
            stored_expiry = row["expires_at"]
            return PartnerBindingRequest(
                binding_request_id=row["binding_request_id"],
                status=row["status"],
                expires_at=stored_expiry,
            )
        except PartnerRepositoryError:
            raise
        except PartnerProviderIdentityCryptoError:
            raise PartnerRepositoryError("partner_identity_conflict", 409) from None
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def link_binding_request(
        self,
        *,
        provider_identity_id: UUID,
        actor_id: UUID,
        binding_request_id: UUID,
        partner_operator_id: UUID,
        reason: str,
        request_id: UUID,
    ) -> PartnerIdentityResolution:
        try:
            with self._connection() as connection:
                self._ensure_identity_key_policy(connection)
                row = connection.execute(
                    "select * from platform_control."
                    "link_partner_binding_request_v54(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        provider_identity_id,
                        actor_id,
                        binding_request_id,
                        partner_operator_id,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return PartnerIdentityResolution(
                subject_id=row["subject_id"],
                partner_operator_id=row["partner_operator_id"],
                partner_organization_id=row["partner_organization_id"],
                binding_request_id=binding_request_id,
                status="linked",
            )
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def reject_binding_request(
        self,
        *,
        actor_id: UUID,
        binding_request_id: UUID,
        reason: str,
        request_id: UUID,
    ) -> PartnerBindingRequest:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control."
                    "reject_partner_binding_request_v54(%s,%s,%s,%s,%s)",
                    (
                        actor_id,
                        binding_request_id,
                        reason,
                        request_id,
                        uuid4(),
                    ),
                ).fetchone()
            if row is None:
                raise PartnerRepositoryError("partner_identity_unavailable")
            return PartnerBindingRequest(
                binding_request_id=row["binding_request_id"],
                status=row["status"],
                expires_at=row["expires_at"],
            )
        except PartnerRepositoryError:
            raise
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def create_external_login_attempt(
        self, *, state_digest: bytes, state_key_version: int, browser_nonce_digest: bytes,
    ) -> UUID:
        try:
            with self._connection() as connection:
                return connection.execute(
                    'select platform_control.create_external_dingtalk_attempt_v123(%s,%s,%s) as id',
                    (state_digest, state_key_version, browser_nonce_digest),
                ).fetchone()['id']
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def consume_external_login_attempt(
        self, *, state_digest: bytes, state_key_version: int, browser_nonce_digest: bytes,
    ) -> UUID:
        try:
            with self._connection() as connection:
                return connection.execute(
                    'select platform_control.consume_external_dingtalk_attempt_v123(%s,%s,%s) as id',
                    (state_digest, state_key_version, browser_nonce_digest),
                ).fetchone()['id']
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def _external_identity_rows(
        self, connection, protected: ProtectedPartnerProviderIdentity,
    ) -> list[dict[str, Any]]:
        candidates = self._lookup_candidates(protected)
        rows = connection.execute(
            'select i.*,o.subject_id,o.partner_organization_id from '
            'platform_control.partner_provider_identities i join '
            'platform_control.partner_operators o using(partner_operator_id) '
            'join unnest(%s::integer[],%s::bytea[]) c(v,h) '
            'on i.lookup_key_version=c.v and i.provider_subject_lookup_hmac=c.h '
            "where i.provider_kind='dingtalk_external'",
            ([version for version, _ in candidates], [value for _, value in candidates]),
        ).fetchall()
        if len(rows) > 1 or any(
            not self.identity_codec.equivalent(self._protected_from_row(row), protected)
            for row in rows
        ):
            raise PartnerRepositoryError('partner_identity_conflict', 409)
        return rows

    def provision_external_identity(
        self, *, protected: ProtectedPartnerProviderIdentity, verified_at: datetime,
        login_attempt_id: UUID, browser_nonce_digest: bytes,
        seal_display: Callable[[UUID], SealedContent], realm_display: SealedContent,
    ) -> PartnerIdentityResolution:
        try:
            candidates = self._lookup_candidates(protected)
            with self._connection() as connection:
                # This transaction holds the policy lock until nickname encryption,
                # identity creation/reuse, grant and audit have all committed.
                self._ensure_identity_key_policy(connection)
                rows = self._external_identity_rows(connection, protected)
                subject_id = rows[0]['subject_id'] if rows else uuid4()
                display = seal_display(subject_id)
                row = connection.execute(
                    'select * from platform_control.provision_external_dingtalk_v123('
                    '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (login_attempt_id, browser_nonce_digest,
                     [v for v, _ in candidates], [h for _, h in candidates],
                     protected.lookup_hmac, protected.lookup_key_version,
                     protected.ciphertext, protected.encryption_key_version,
                     subject_id, display.ciphertext, display.key_version,
                     realm_display.ciphertext, realm_display.key_version, verified_at),
                ).fetchone()
            if row is None:
                raise PartnerRepositoryError('partner_identity_unavailable')
            return PartnerIdentityResolution(
                subject_id=row['subject_id'], partner_operator_id=row['partner_operator_id'],
                partner_organization_id=row['partner_organization_id'], binding_request_id=None,
                status='linked',
            )
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=True)

    def external_verified_at(
        self, protected: ProtectedPartnerProviderIdentity,
    ) -> datetime | None:
        try:
            with self._connection() as connection:
                rows = self._external_identity_rows(connection, protected)
            if not rows or rows[0]['revoked_at'] is not None:
                return None
            return rows[0]['verified_at']
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)

    def external_identity_detail(
        self, *, actor_id: UUID, subject_id: UUID, request_id: UUID,
    ) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    'select * from platform_control.read_external_dingtalk_identity_v123(%s,%s,%s)',
                    (actor_id, subject_id, request_id),
                ).fetchall()
            if len(rows) != 1:
                raise PartnerRepositoryError('provider_identity_inactive', 404)
            return rows[0]
        except psycopg.Error as error:
            self._raise_database_error(error, mutation=False)
