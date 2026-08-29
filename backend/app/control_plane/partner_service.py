from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.execution_relay.content_crypto import ContentCodec, ContentCryptoError

from .partner_identity_crypto import (
    PartnerProviderIdentityCodec,
    PartnerProviderIdentityCryptoError,
)
from .partner_models import (
    PartnerAccessDecision,
    PartnerBindingRequest,
    PartnerIdentityError,
    PartnerIdentityResolution,
    PartnerOperator,
    PartnerOrganization,
    PartnerStatus,
    VerifiedProviderSubject,
)
from .partner_repository import PartnerRepositoryError


class PartnerService:
    def __init__(
        self,
        repository,
        *,
        identity_codec: PartnerProviderIdentityCodec | None = None,
        content_codec: ContentCodec,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        selected_identity_codec = identity_codec or getattr(
            repository, "identity_codec", None
        )
        if not isinstance(selected_identity_codec, PartnerProviderIdentityCodec):
            raise TypeError("partner provider identity codec required")
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        self.repository = repository
        self.identity_codec = selected_identity_codec
        self.content_codec = content_codec
        self._now = now or (lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return (
            "PartnerService(repository=<redacted>, identity_codec=<redacted>, "
            "content_codec=<redacted>)"
        )

    @staticmethod
    def _uuid(value: UUID, code: str) -> UUID:
        if not isinstance(value, UUID):
            raise PartnerIdentityError(code, 422)
        return value

    @staticmethod
    def _text(value: str, code: str, *, maximum: int = 512) -> str:
        if not isinstance(value, str):
            raise PartnerIdentityError(code, 422)
        normalized = value.strip()
        if not normalized or "\0" in normalized or len(normalized) > maximum:
            raise PartnerIdentityError(code, 422)
        return normalized

    @staticmethod
    def _status(value: str | PartnerStatus) -> PartnerStatus:
        try:
            return PartnerStatus(value)
        except (TypeError, ValueError):
            raise PartnerIdentityError("partner_status_invalid", 422) from None

    @staticmethod
    def _translate(error: PartnerRepositoryError) -> PartnerIdentityError:
        return PartnerIdentityError(error.code, error.status_code)

    def list_organizations(self) -> tuple[PartnerOrganization, ...]:
        try:
            return self.repository.list_organizations()
        except PartnerRepositoryError as error:
            raise self._translate(error) from None

    def create_organization(
        self,
        *,
        actor_id: UUID,
        display_name: str,
        reason: str,
        request_id: UUID,
    ) -> PartnerOrganization:
        actor = self._uuid(actor_id, "owner_required")
        request = self._uuid(request_id, "request_id_invalid")
        selected_name = self._text(display_name, "display_name_invalid")
        selected_reason = self._text(reason, "reason_invalid")
        organization_id = uuid4()
        try:
            sealed = self.content_codec.seal_json(
                f"partner-organization-display:{organization_id}",
                {"display_name": selected_name},
            )
            return self.repository.create_organization(
                partner_organization_id=organization_id,
                actor_id=actor,
                display_name_ciphertext=sealed.ciphertext,
                display_name_key_version=sealed.key_version,
                reason=selected_reason,
                request_id=request,
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None
        except ContentCryptoError:
            raise PartnerIdentityError("partner_identity_unavailable") from None

    def create_operator(
        self,
        *,
        actor_id: UUID,
        partner_organization_id: UUID,
        display_name: str,
        reason: str,
        request_id: UUID,
    ) -> PartnerOperator:
        actor = self._uuid(actor_id, "owner_required")
        organization_id = self._uuid(
            partner_organization_id, "partner_organization_invalid"
        )
        request = self._uuid(request_id, "request_id_invalid")
        selected_name = self._text(display_name, "display_name_invalid")
        selected_reason = self._text(reason, "reason_invalid")
        partner_operator_id = uuid4()
        subject_id = uuid4()
        try:
            sealed = self.content_codec.seal_json(
                f"agent-subject-display:{subject_id}",
                {"display_name": selected_name},
            )
            return self.repository.create_operator(
                partner_operator_id=partner_operator_id,
                subject_id=subject_id,
                partner_organization_id=organization_id,
                actor_id=actor,
                display_name_ciphertext=sealed.ciphertext,
                display_name_key_version=sealed.key_version,
                reason=selected_reason,
                request_id=request,
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None
        except ContentCryptoError:
            raise PartnerIdentityError("partner_identity_unavailable") from None

    def set_organization_status(
        self,
        *,
        actor_id: UUID,
        organization_id: UUID,
        status: str | PartnerStatus,
        reason: str,
        request_id: UUID,
    ) -> PartnerOrganization:
        try:
            return self.repository.set_organization_status(
                actor_id=self._uuid(actor_id, "owner_required"),
                partner_organization_id=self._uuid(
                    organization_id, "partner_organization_invalid"
                ),
                status=self._status(status).value,
                reason=self._text(reason, "reason_invalid"),
                request_id=self._uuid(request_id, "request_id_invalid"),
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None

    def set_operator_status(
        self,
        *,
        actor_id: UUID,
        operator_id: UUID,
        status: str | PartnerStatus,
        reason: str,
        request_id: UUID,
    ) -> PartnerOperator:
        try:
            return self.repository.set_operator_status(
                actor_id=self._uuid(actor_id, "owner_required"),
                partner_operator_id=self._uuid(operator_id, "partner_operator_invalid"),
                status=self._status(status).value,
                reason=self._text(reason, "reason_invalid"),
                request_id=self._uuid(request_id, "request_id_invalid"),
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None

    def grant_fae(
        self,
        *,
        actor_id: UUID,
        operator_id: UUID,
        reason: str,
        request_id: UUID,
    ) -> None:
        try:
            self.repository.grant_fae(
                actor_id=self._uuid(actor_id, "owner_required"),
                partner_operator_id=self._uuid(operator_id, "partner_operator_invalid"),
                reason=self._text(reason, "reason_invalid"),
                request_id=self._uuid(request_id, "request_id_invalid"),
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None

    def revoke_fae(
        self,
        *,
        actor_id: UUID,
        operator_id: UUID,
        reason: str,
        request_id: UUID,
    ) -> None:
        try:
            self.repository.revoke_fae(
                actor_id=self._uuid(actor_id, "owner_required"),
                partner_operator_id=self._uuid(operator_id, "partner_operator_invalid"),
                reason=self._text(reason, "reason_invalid"),
                request_id=self._uuid(request_id, "request_id_invalid"),
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None

    def decide_fae_access(self, subject_id: UUID) -> PartnerAccessDecision:
        selected_subject = self._uuid(subject_id, "partner_subject_invalid")
        try:
            result = self.repository.decide_fae_access(selected_subject)
            reason = result if isinstance(result, str) else result.reason
            if reason not in {
                "active",
                "subject_inactive",
                "organization_inactive",
                "operator_inactive",
                "fae_access_denied",
            }:
                raise PartnerIdentityError("partner_identity_unavailable")
            return PartnerAccessDecision(
                allowed=reason == "active",
                reason=reason,
                subject_id=selected_subject if reason == "active" else None,
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None

    def record_binding_request(
        self, verified: VerifiedProviderSubject
    ) -> PartnerBindingRequest:
        if not isinstance(verified, VerifiedProviderSubject):
            raise PartnerIdentityError("verified_partner_identity_required", 422)
        if (
            not isinstance(verified.verified_at, datetime)
            or verified.verified_at.tzinfo is None
        ):
            raise PartnerIdentityError("verified_partner_identity_required", 422)
        binding_request_id = uuid4()
        try:
            protected = self.identity_codec.seal(
                verified.provider_kind, verified.provider_subject
            )
            display_ciphertext = None
            display_key_version = None
            if verified.display_name is not None:
                display_name = self._text(verified.display_name, "display_name_invalid")
                display = self.content_codec.seal_json(
                    f"partner-binding-display:{binding_request_id}",
                    {"display_name": display_name},
                )
                display_ciphertext = display.ciphertext
                display_key_version = display.key_version
            return self.repository.record_binding_request(
                binding_request_id=binding_request_id,
                protected_identity=protected,
                display_name_ciphertext=display_ciphertext,
                display_name_key_version=display_key_version,
                verified_at=verified.verified_at,
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None
        except (ContentCryptoError, PartnerProviderIdentityCryptoError):
            raise PartnerIdentityError("partner_identity_unavailable") from None

    def resolve_verified_identity(
        self, verified: VerifiedProviderSubject
    ) -> PartnerIdentityResolution:
        if not isinstance(verified, VerifiedProviderSubject):
            raise PartnerIdentityError("verified_partner_identity_required", 422)
        try:
            protected = self.identity_codec.seal(
                verified.provider_kind, verified.provider_subject
            )
            resolved = self.repository.resolve_provider_identity(protected)
            if resolved is not None:
                if resolved.status != "linked":
                    raise PartnerIdentityError("provider_identity_inactive", 403)
                return resolved
            try:
                pending = self.record_binding_request(verified)
            except PartnerIdentityError as error:
                if error.code != "partner_identity_already_linked":
                    raise
                resolved = self.repository.resolve_provider_identity(protected)
                if resolved is None:
                    raise PartnerIdentityError("partner_identity_unavailable") from None
                if resolved.status != "linked":
                    raise PartnerIdentityError("provider_identity_inactive", 403)
                return resolved
            return PartnerIdentityResolution(
                subject_id=None,
                partner_operator_id=None,
                partner_organization_id=None,
                binding_request_id=pending.binding_request_id,
                status=str(pending.status),
            )
        except PartnerIdentityError:
            raise
        except PartnerRepositoryError as error:
            raise self._translate(error) from None
        except PartnerProviderIdentityCryptoError:
            raise PartnerIdentityError("partner_identity_unavailable") from None

    def link_binding_request(
        self,
        *,
        actor_id: UUID,
        binding_request_id: UUID,
        operator_id: UUID,
        reason: str,
        request_id: UUID,
    ) -> PartnerIdentityResolution:
        try:
            return self.repository.link_binding_request(
                provider_identity_id=uuid4(),
                actor_id=self._uuid(actor_id, "owner_required"),
                binding_request_id=self._uuid(
                    binding_request_id, "binding_request_invalid"
                ),
                partner_operator_id=self._uuid(operator_id, "partner_operator_invalid"),
                reason=self._text(reason, "reason_invalid"),
                request_id=self._uuid(request_id, "request_id_invalid"),
            )
        except PartnerRepositoryError as error:
            raise self._translate(error) from None
