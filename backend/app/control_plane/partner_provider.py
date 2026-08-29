from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import quote
from uuid import UUID

from .auth import AuthSecrets
from .partner_models import (
    PartnerIdentityError,
    PartnerIdentityResolution,
    VerifiedProviderSubject,
)

_PROVIDER_KIND = re.compile(r"[a-z][a-z0-9_-]{0,127}\Z")
_CALLBACK_PATH = re.compile(r"/partner-auth/[A-Za-z0-9][A-Za-z0-9/_-]{0,127}\Z")
_FIXED_FAE_RETURN_PATH = "/app/"


def validate_partner_callback(method: str, path: str) -> tuple[str, str]:
    selected_method = method.upper() if isinstance(method, str) else ""
    if selected_method not in {"GET", "POST"}:
        raise ValueError("partner_callback_method_invalid")
    if (
        not isinstance(path, str)
        or _CALLBACK_PATH.fullmatch(path) is None
        or path.endswith("/")
        or "//" in path
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise ValueError("partner_callback_path_invalid")
    return selected_method, path


@runtime_checkable
class PartnerIdentityProvider(Protocol):
    kind: str

    def begin_auth(self, state: str) -> str: ...

    async def finish_auth(
        self, callback: Mapping[str, str]
    ) -> VerifiedProviderSubject: ...

    async def check_subject(
        self, provider_subject: str
    ) -> Literal["active", "inactive"]: ...


class ReferencePartnerIdentityProvider:
    kind = "reference"

    def __init__(self, identities: Mapping[str, tuple[str, str]]) -> None:
        self._identities = dict(identities)

    def __repr__(self) -> str:
        return "ReferencePartnerIdentityProvider(identities=<redacted>)"

    def begin_auth(self, state: str) -> str:
        return f"/partner-auth/reference?state={quote(state, safe='')}"

    async def finish_auth(self, callback: Mapping[str, str]) -> VerifiedProviderSubject:
        code = callback.get("code", "")
        if not isinstance(code, str) or code not in self._identities:
            raise PartnerIdentityError("partner_auth_invalid", 401)
        subject, display_name = self._identities[code]
        return VerifiedProviderSubject(
            provider_kind=self.kind,
            provider_subject=subject,
            verified_at=datetime.now(UTC),
            display_name=display_name,
        )

    async def check_subject(
        self, provider_subject: str
    ) -> Literal["active", "inactive"]:
        active = any(
            subject == provider_subject
            for subject, _display_name in self._identities.values()
        )
        return "active" if active else "inactive"


@dataclass(frozen=True)
class _ProviderRegistration:
    factory: Callable[[], PartnerIdentityProvider] = field(repr=False)
    production_release: bool


_PROVIDERS: dict[str, _ProviderRegistration] = {}


def _provider_boundary_error(
    error: Exception, *, callback: bool = False
) -> PartnerIdentityError:
    callback_errors = {
        "authentication_cancelled": 401,
        "partner_auth_invalid": 401,
    }
    if callback and isinstance(error, PartnerIdentityError):
        status_code = callback_errors.get(error.code)
        if status_code is not None:
            return PartnerIdentityError(error.code, status_code)
    return PartnerIdentityError("partner_identity_unavailable")


def register_partner_provider(
    kind: str,
    factory: Callable[[], PartnerIdentityProvider],
    *,
    production_release: bool,
) -> None:
    if not isinstance(kind, str) or _PROVIDER_KIND.fullmatch(kind) is None:
        raise ValueError("partner_provider_kind_invalid")
    if not callable(factory):
        raise TypeError("partner provider factory required")
    if kind in _PROVIDERS:
        raise ValueError("partner_provider_already_registered")
    _PROVIDERS[kind] = _ProviderRegistration(factory, production_release)


def unregister_partner_provider(kind: str) -> None:
    if kind == "reference":
        raise ValueError("partner_reference_provider_registration_required")
    _PROVIDERS.pop(kind, None)


def partner_provider_registered(kind: str) -> bool:
    return kind in _PROVIDERS


def partner_provider_release_registered(kind: str) -> bool:
    registration = _PROVIDERS.get(kind)
    return bool(registration and registration.production_release)


def create_registered_partner_provider(kind: str) -> PartnerIdentityProvider:
    registration = _PROVIDERS.get(kind)
    if registration is None:
        raise PartnerIdentityError("partner_identity_unavailable")
    try:
        provider = registration.factory()
    except Exception:  # noqa: BLE001 - adapter failures cross a redacted boundary
        raise PartnerIdentityError("partner_identity_unavailable") from None
    if not isinstance(provider, PartnerIdentityProvider) or provider.kind != kind:
        raise PartnerIdentityError("partner_identity_unavailable")
    return provider


@dataclass(frozen=True)
class StartedPartnerAuthentication:
    authorization_url: str = field(repr=False)
    return_path: str = _FIXED_FAE_RETURN_PATH


@dataclass(frozen=True)
class CompletedPartnerAuthentication:
    subject_id: UUID
    status: str = "partner_authenticated"
    return_path: str = _FIXED_FAE_RETURN_PATH


class PartnerAuthenticationBroker:
    def __init__(
        self,
        provider: PartnerIdentityProvider,
        service,
        *,
        state_secrets: AuthSecrets,
    ) -> None:
        if not isinstance(provider, PartnerIdentityProvider):
            raise TypeError("partner identity provider required")
        if not isinstance(state_secrets, AuthSecrets):
            raise TypeError("partner state secrets required")
        if _PROVIDER_KIND.fullmatch(provider.kind) is None:
            raise ValueError("partner_provider_kind_invalid")
        self.provider = provider
        self.service = service
        self.state_secrets = state_secrets

    def __repr__(self) -> str:
        return (
            "PartnerAuthenticationBroker(provider=<redacted>, "
            "service=<redacted>, state_secrets=<redacted>)"
        )

    def begin_auth(self) -> StartedPartnerAuthentication:
        state = self.state_secrets.random_token()
        digest = self.state_secrets.digest("oauth-state", state)
        try:
            self.service.create_login_attempt(
                provider_kind=self.provider.kind,
                state_digest=digest,
                state_key_version=self.state_secrets.key_version,
            )
        except PartnerIdentityError:
            raise
        except Exception:  # noqa: BLE001 - storage failures are redacted
            raise PartnerIdentityError("partner_identity_unavailable") from None
        try:
            authorization_url = self.provider.begin_auth(state)
        except Exception as error:  # noqa: BLE001 - adapter failures are redacted
            raise _provider_boundary_error(error) from None
        if not isinstance(authorization_url, str) or not authorization_url:
            raise PartnerIdentityError("partner_identity_unavailable")
        return StartedPartnerAuthentication(authorization_url=authorization_url)

    async def finish_auth(
        self, callback: Mapping[str, str]
    ) -> CompletedPartnerAuthentication:
        state = callback.get("state", "")
        if not isinstance(state, str) or not state:
            raise PartnerIdentityError("partner_auth_state_invalid", 401)
        try:
            self.service.consume_login_attempt(
                provider_kind=self.provider.kind,
                state_digest=self.state_secrets.digest("oauth-state", state),
                state_key_version=self.state_secrets.key_version,
            )
        except PartnerIdentityError:
            raise
        except Exception:  # noqa: BLE001 - storage failures are redacted
            raise PartnerIdentityError("partner_identity_unavailable") from None

        if callback.get("error"):
            raise PartnerIdentityError("authentication_cancelled", 401)

        try:
            verified = await self.provider.finish_auth(callback)
        except Exception as error:  # noqa: BLE001 - adapter failures are redacted
            raise _provider_boundary_error(error, callback=True) from None
        if (
            not isinstance(verified, VerifiedProviderSubject)
            or verified.provider_kind != self.provider.kind
        ):
            raise PartnerIdentityError("partner_auth_invalid", 401)

        try:
            provider_status = await self.provider.check_subject(
                verified.provider_subject
            )
        except Exception as error:  # noqa: BLE001 - adapter failures are redacted
            raise _provider_boundary_error(error) from None
        if provider_status == "inactive":
            raise PartnerIdentityError("provider_identity_inactive", 403)
        if provider_status != "active":
            raise PartnerIdentityError("partner_identity_unavailable")

        try:
            resolution = self.service.resolve_verified_identity(verified)
        except PartnerIdentityError:
            raise
        except Exception:  # noqa: BLE001 - storage failures are redacted
            raise PartnerIdentityError("partner_identity_unavailable") from None
        return self._complete_resolution(resolution)

    def _complete_resolution(
        self, resolution: PartnerIdentityResolution
    ) -> CompletedPartnerAuthentication:
        if not isinstance(resolution, PartnerIdentityResolution):
            raise PartnerIdentityError("partner_identity_unavailable")
        if resolution.subject_id is None:
            if (
                resolution.status == "pending"
                and resolution.binding_request_id is not None
            ):
                raise PartnerIdentityError("partner_binding_required", 403)
            raise PartnerIdentityError("partner_identity_unavailable")
        if resolution.status != "linked":
            raise PartnerIdentityError("provider_identity_inactive", 403)
        decision = self.service.decide_fae_access(resolution.subject_id)
        if not getattr(decision, "allowed", False):
            reason = getattr(decision, "reason", "partner_identity_unavailable")
            if reason in {
                "subject_inactive",
                "organization_inactive",
                "operator_inactive",
                "fae_access_denied",
            }:
                raise PartnerIdentityError(reason, 403)
            raise PartnerIdentityError("partner_identity_unavailable")
        return CompletedPartnerAuthentication(subject_id=resolution.subject_id)


register_partner_provider(
    "reference",
    lambda: ReferencePartnerIdentityProvider({}),
    production_release=False,
)
