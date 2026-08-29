from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from itertools import pairwise

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import IdentityCryptoError, IdentityKeyring


class PartnerProviderIdentityCryptoError(IdentityCryptoError):
    """Stable partner-identity crypto error without protected values."""


@dataclass(frozen=True)
class ProtectedPartnerProviderIdentity:
    provider_kind: str = field(repr=False)
    provider_subject_lookup_hmac: bytes = field(repr=False)
    lookup_key_version: int
    provider_subject_ciphertext: bytes = field(repr=False)
    encryption_key_version: int

    @property
    def lookup_hmac(self) -> bytes:
        return self.provider_subject_lookup_hmac

    @property
    def ciphertext(self) -> bytes:
        return self.provider_subject_ciphertext

    def __repr__(self) -> str:
        return (
            "ProtectedPartnerProviderIdentity(provider_kind=<redacted>, "
            "provider_subject_lookup_hmac=<redacted>, "
            f"lookup_key_version={self.lookup_key_version!r}, "
            "provider_subject_ciphertext=<redacted>, "
            f"encryption_key_version={self.encryption_key_version!r})"
        )


def _normalize_provider_kind(provider_kind: str) -> str:
    try:
        if (
            not isinstance(provider_kind, str)
            or not provider_kind
            or provider_kind != provider_kind.strip()
            or ":" in provider_kind
            or "\0" in provider_kind
        ):
            raise ValueError
        provider_kind.encode("utf-8")
        return provider_kind
    except (TypeError, ValueError, UnicodeError):
        raise PartnerProviderIdentityCryptoError(
            "partner provider identity invalid"
        ) from None


def _normalize_provider_subject(provider_subject: str) -> str:
    try:
        if not isinstance(provider_subject, str):
            raise TypeError
        normalized = provider_subject.strip()
        if not normalized or "\0" in normalized:
            raise ValueError
        normalized.encode("utf-8")
        return normalized
    except (TypeError, ValueError, UnicodeError):
        raise PartnerProviderIdentityCryptoError(
            "partner provider identity invalid"
        ) from None


class PartnerProviderIdentityCodec:
    """Purpose-separated Partner Provider identity protection boundary."""

    def __init__(
        self,
        encryption: IdentityKeyring,
        hmac_keyring: IdentityKeyring,
    ) -> None:
        if encryption.purpose != "partner-provider-encryption" or (
            hmac_keyring.purpose != "partner-provider-lookup-hmac"
        ):
            raise PartnerProviderIdentityCryptoError(
                "partner identity keyring purpose invalid"
            )
        if encryption.key_lengths != {32} or hmac_keyring.key_lengths != {32}:
            raise PartnerProviderIdentityCryptoError(
                "partner identity key length invalid"
            )
        if encryption.transition_versions is not None:
            raise PartnerProviderIdentityCryptoError(
                "partner identity transition invalid"
            )
        transition_versions = hmac_keyring.transition_versions
        previous_versions = [
            version
            for version in hmac_keyring._keys
            if version < hmac_keyring.active_version
        ]
        previous_version = max(previous_versions, default=None)
        if (
            transition_versions is None
            or len(transition_versions) not in {1, 2, 3}
            or tuple(sorted(set(transition_versions))) != transition_versions
            or tuple(sorted(hmac_keyring._keys)) != transition_versions
            or hmac_keyring.active_version not in transition_versions
            or (
                previous_version is not None
                and previous_version not in transition_versions
            )
            or any(
                following != preceding + 1
                for preceding, following in pairwise(transition_versions)
            )
        ):
            raise PartnerProviderIdentityCryptoError(
                "partner identity transition invalid"
            )
        if encryption.overlaps(hmac_keyring):
            raise PartnerProviderIdentityCryptoError(
                "partner identity key separation invalid"
            )
        self.encryption = encryption
        self.hmac_keyring = hmac_keyring

    def __repr__(self) -> str:
        return (
            "PartnerProviderIdentityCodec(encryption=<redacted>, "
            "hmac_keyring=<redacted>)"
        )

    @staticmethod
    def _lookup_input(provider_kind: str, provider_subject: str) -> bytes:
        return f"partner-provider:{provider_kind}:{provider_subject}".encode()

    @staticmethod
    def _aad(provider_kind: str, version: int) -> bytes:
        return f"partner-provider:{provider_kind}:v{version}".encode()

    def _lookup(self, provider_kind: str, provider_subject: str, version: int) -> bytes:
        try:
            return hmac.digest(
                self.hmac_keyring.key_for_version(version),
                self._lookup_input(provider_kind, provider_subject),
                hashlib.sha256,
            )
        except IdentityCryptoError:
            raise PartnerProviderIdentityCryptoError(
                "partner identity key version unavailable"
            ) from None

    def seal(
        self, provider_kind: str, provider_subject: str
    ) -> ProtectedPartnerProviderIdentity:
        kind = _normalize_provider_kind(provider_kind)
        normalized = _normalize_provider_subject(provider_subject)
        try:
            encryption_version = self.encryption.active_version
            nonce = secrets.token_bytes(12)
            ciphertext = nonce + AESGCM(self.encryption.active_key).encrypt(
                nonce,
                normalized.encode("utf-8"),
                self._aad(kind, encryption_version),
            )
            return ProtectedPartnerProviderIdentity(
                provider_kind=kind,
                provider_subject_lookup_hmac=self._lookup(
                    kind, normalized, self.hmac_keyring.active_version
                ),
                lookup_key_version=self.hmac_keyring.active_version,
                provider_subject_ciphertext=ciphertext,
                encryption_key_version=encryption_version,
            )
        except PartnerProviderIdentityCryptoError:
            raise
        except (OSError, TypeError, ValueError, UnicodeError):
            raise PartnerProviderIdentityCryptoError(
                "partner identity encrypt failed"
            ) from None

    def unseal(self, protected: ProtectedPartnerProviderIdentity) -> str:
        try:
            kind = _normalize_provider_kind(protected.provider_kind)
            ciphertext = protected.provider_subject_ciphertext
            version = protected.encryption_key_version
            if (
                not isinstance(ciphertext, bytes)
                or len(ciphertext) < 28
                or isinstance(version, bool)
                or not isinstance(version, int)
                or version <= 0
            ):
                raise ValueError
            key = self.encryption.key_for_version(version)
            plaintext = AESGCM(key).decrypt(
                ciphertext[:12],
                ciphertext[12:],
                self._aad(kind, version),
            )
            return _normalize_provider_subject(plaintext.decode("utf-8"))
        except (
            AttributeError,
            IdentityCryptoError,
            InvalidTag,
            PartnerProviderIdentityCryptoError,
            TypeError,
            ValueError,
            UnicodeError,
        ):
            raise PartnerProviderIdentityCryptoError(
                "partner identity decrypt failed"
            ) from None

    def lookup_candidates(
        self, provider_kind: str, provider_subject: str
    ) -> tuple[tuple[int, bytes], ...]:
        kind = _normalize_provider_kind(provider_kind)
        normalized = _normalize_provider_subject(provider_subject)
        return tuple(
            (version, self._lookup(kind, normalized, version))
            for version in self.hmac_keyring.transition_versions or ()
        )

    def matches_lookup(
        self,
        *,
        provider_kind: str,
        provider_subject: str,
        lookup_hmac: bytes,
        lookup_key_version: int,
    ) -> bool:
        try:
            kind = _normalize_provider_kind(provider_kind)
            normalized = _normalize_provider_subject(provider_subject)
            expected = self._lookup(kind, normalized, lookup_key_version)
            return isinstance(lookup_hmac, bytes) and hmac.compare_digest(
                lookup_hmac, expected
            )
        except PartnerProviderIdentityCryptoError:
            return False

    def equivalent(
        self,
        first: ProtectedPartnerProviderIdentity,
        second: ProtectedPartnerProviderIdentity,
    ) -> bool:
        if first.provider_kind != second.provider_kind:
            return False
        return hmac.compare_digest(
            self.unseal(first).encode("utf-8"),
            self.unseal(second).encode("utf-8"),
        )
