from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
from types import MappingProxyType
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class IdentityCryptoError(RuntimeError):
    """Stable cryptographic boundary error without protected values."""


@dataclass(frozen=True)
class IdentityKeyring:
    active_version: int
    purpose: str
    _keys: Mapping[int, bytes] = field(repr=False)
    transition_versions: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "_keys", MappingProxyType(dict(self._keys)))

    def __repr__(self) -> str:
        versions = tuple(sorted(self._keys))
        return (
            f"IdentityKeyring(active_version={self.active_version!r}, "
            f"purpose={self.purpose!r}, versions={versions!r}, "
            f"transition_versions={self.transition_versions!r}, "
            "keys=<redacted>)"
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        expected_purpose: str,
        expected_key_length: int,
    ) -> IdentityKeyring:
        """Load one purpose-bound, current-user-only versioned keyring."""
        try:
            if not expected_purpose or expected_key_length <= 0:
                raise ValueError
            keyring_path = Path(path)
            if not keyring_path.is_absolute():
                raise ValueError
            metadata = keyring_path.lstat()
            if (
                keyring_path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError

            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(keyring_path, flags)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise ValueError
                payload = os.read(descriptor, 65_537)
            finally:
                os.close(descriptor)
            if len(payload) > 65_536:
                raise ValueError

            document = json.loads(payload.decode("utf-8"))
            if not isinstance(document, dict) or set(document) not in (
                {"purpose", "active_version", "keys"},
                {
                    "purpose",
                    "active_version",
                    "keys",
                    "transition_versions",
                },
            ):
                raise ValueError
            if document["purpose"] != expected_purpose:
                raise ValueError
            active_version = document["active_version"]
            encoded_keys = document["keys"]
            encoded_transition_versions = document.get("transition_versions")
            if (
                isinstance(active_version, bool)
                or not isinstance(active_version, int)
                or active_version <= 0
                or not isinstance(encoded_keys, dict)
                or not encoded_keys
            ):
                raise ValueError
            transition_versions: tuple[int, ...] | None = None
            if encoded_transition_versions is not None:
                if not isinstance(encoded_transition_versions, list) or any(
                    isinstance(version, bool)
                    or not isinstance(version, int)
                    or version <= 0
                    for version in encoded_transition_versions
                ):
                    raise ValueError
                transition_versions = tuple(encoded_transition_versions)

            keys: dict[int, bytes] = {}
            for encoded_version, encoded_key in encoded_keys.items():
                if (
                    not isinstance(encoded_version, str)
                    or not encoded_version.isascii()
                    or not encoded_version.isdigit()
                    or str(int(encoded_version)) != encoded_version
                    or int(encoded_version) <= 0
                    or not isinstance(encoded_key, str)
                ):
                    raise ValueError
                key = base64.b64decode(encoded_key, validate=True)
                if len(key) != expected_key_length:
                    raise ValueError
                keys[int(encoded_version)] = key
            if active_version not in keys:
                raise ValueError
            return cls(
                active_version=active_version,
                purpose=expected_purpose,
                _keys=keys,
                transition_versions=transition_versions,
            )
        except IdentityCryptoError:
            raise
        except (
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            binascii.Error,
        ):
            raise IdentityCryptoError("identity keyring unavailable") from None

    @property
    def active_key(self) -> bytes:
        return self._keys[self.active_version]

    @property
    def key_lengths(self) -> frozenset[int]:
        return frozenset(len(key) for key in self._keys.values())

    def overlaps(self, other: IdentityKeyring) -> bool:
        return any(
            hmac.compare_digest(first, second)
            for first in self._keys.values()
            for second in other._keys.values()
        )

    def key_for_version(self, version: int) -> bytes:
        try:
            return self._keys[version]
        except (KeyError, TypeError):
            raise IdentityCryptoError("identity key version unavailable") from None


@dataclass(frozen=True)
class ProtectedProviderId:
    subject_kind: str
    lookup_hmac: bytes = field(repr=False)
    lookup_key_version: int
    ciphertext: bytes = field(repr=False)
    encryption_key_version: int

    def __repr__(self) -> str:
        return (
            f"ProtectedProviderId(subject_kind={self.subject_kind!r}, "
            "lookup_hmac=<redacted>, "
            f"lookup_key_version={self.lookup_key_version!r}, "
            "ciphertext=<redacted>, "
            f"encryption_key_version={self.encryption_key_version!r})"
        )


def normalize_provider_id(provider_id: str) -> str:
    try:
        if not isinstance(provider_id, str):
            raise ValueError
        normalized = provider_id.strip()
        if not normalized or "\0" in normalized:
            raise ValueError
        normalized.encode("utf-8")
        return normalized
    except (TypeError, ValueError, UnicodeError):
        raise IdentityCryptoError("provider identity invalid") from None


def _normalize_subject_kind(subject_kind: str) -> str:
    try:
        if (
            not isinstance(subject_kind, str)
            or not subject_kind
            or subject_kind != subject_kind.strip()
            or ":" in subject_kind
            or "\0" in subject_kind
        ):
            raise ValueError
        subject_kind.encode("utf-8")
        return subject_kind
    except (TypeError, ValueError, UnicodeError):
        raise IdentityCryptoError("provider identity invalid") from None


class ProviderIdentityCodec:
    """Protect provider IDs under one deployment-wide HMAC transition window.

    During an N to N+1 rollout, every participant must use the same ordered
    contiguous transition versions: (N-1, N, N+1), omitting N-1 when absent.
    This lets old and new nodes derive, query, and lock the identical lookup
    candidates while retaining each active version's previous lookup.
    """

    def __init__(
        self,
        encryption: IdentityKeyring,
        hmac_keyring: IdentityKeyring,
    ) -> None:
        if encryption.purpose != "provider-encryption":
            raise IdentityCryptoError("identity keyring purpose invalid")
        if hmac_keyring.purpose != "provider-lookup-hmac":
            raise IdentityCryptoError("identity keyring purpose invalid")
        if encryption.key_lengths != {32} or hmac_keyring.key_lengths != {32}:
            raise IdentityCryptoError("identity key length invalid")
        if encryption.transition_versions is not None:
            raise IdentityCryptoError("identity transition invalid")
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
                version not in hmac_keyring._keys
                for version in transition_versions
            )
            or any(
                following != preceding + 1
                for preceding, following in zip(
                    transition_versions, transition_versions[1:]
                )
            )
        ):
            raise IdentityCryptoError("identity transition invalid")
        if encryption.overlaps(hmac_keyring):
            raise IdentityCryptoError("identity key separation invalid")
        self.encryption = encryption
        self.hmac = hmac_keyring

    def __repr__(self) -> str:
        return (
            "ProviderIdentityCodec(encryption=<redacted>, "
            "hmac_keyring=<redacted>)"
        )

    @property
    def hmac_keyring(self) -> IdentityKeyring:
        return self.hmac

    @staticmethod
    def _lookup_input(subject_kind: str, normalized: str) -> bytes:
        return f"dingtalk:{subject_kind}:{normalized}".encode("utf-8")

    @staticmethod
    def _aad(subject_kind: str, version: int) -> bytes:
        return f"dingtalk:{subject_kind}:v{version}".encode("utf-8")

    def _lookup(
        self,
        subject_kind: str,
        normalized: str,
        version: int,
    ) -> bytes:
        return hmac.digest(
            self.hmac.key_for_version(version),
            self._lookup_input(subject_kind, normalized),
            hashlib.sha256,
        )

    def seal(self, subject_kind: str, provider_id: str) -> ProtectedProviderId:
        kind = _normalize_subject_kind(subject_kind)
        normalized = normalize_provider_id(provider_id)
        try:
            encryption_version = self.encryption.active_version
            nonce = secrets.token_bytes(12)
            encrypted = AESGCM(self.encryption.active_key).encrypt(
                nonce,
                normalized.encode("utf-8"),
                self._aad(kind, encryption_version),
            )
            return ProtectedProviderId(
                subject_kind=kind,
                lookup_hmac=self._lookup(
                    kind, normalized, self.hmac.active_version
                ),
                lookup_key_version=self.hmac.active_version,
                ciphertext=nonce + encrypted,
                encryption_key_version=encryption_version,
            )
        except IdentityCryptoError:
            raise
        except (TypeError, ValueError, UnicodeError):
            raise IdentityCryptoError("identity encrypt failed") from None

    def unseal(self, protected: ProtectedProviderId) -> str:
        try:
            kind = _normalize_subject_kind(protected.subject_kind)
            if (
                not isinstance(protected.ciphertext, bytes)
                or len(protected.ciphertext) < 28
                or isinstance(protected.encryption_key_version, bool)
                or protected.encryption_key_version <= 0
            ):
                raise ValueError
            key = self.encryption.key_for_version(
                protected.encryption_key_version
            )
            plaintext = AESGCM(key).decrypt(
                protected.ciphertext[:12],
                protected.ciphertext[12:],
                self._aad(kind, protected.encryption_key_version),
            )
            return normalize_provider_id(plaintext.decode("utf-8"))
        except (
            AttributeError,
            IdentityCryptoError,
            InvalidTag,
            TypeError,
            ValueError,
            UnicodeError,
        ):
            raise IdentityCryptoError("identity decrypt failed") from None

    def lookup_candidates(
        self, subject_kind: str, provider_id: str
    ) -> tuple[tuple[int, bytes], ...]:
        kind = _normalize_subject_kind(subject_kind)
        normalized = normalize_provider_id(provider_id)
        return tuple(
            (version, self._lookup(kind, normalized, version))
            for version in self.hmac.transition_versions or ()
        )

    def matches_lookup(
        self,
        *,
        subject_kind: str,
        provider_id: str,
        lookup_hmac: bytes,
        lookup_key_version: int,
    ) -> bool:
        try:
            kind = _normalize_subject_kind(subject_kind)
            normalized = normalize_provider_id(provider_id)
            expected = self._lookup(
                kind, normalized, lookup_key_version
            )
            return hmac.compare_digest(expected, lookup_hmac)
        except (IdentityCryptoError, TypeError, ValueError):
            return False

    def equivalent(
        self, first: ProtectedProviderId, second: ProtectedProviderId
    ) -> bool:
        first_value = self.unseal(first).encode("utf-8")
        second_value = self.unseal(second).encode("utf-8")
        return (
            hmac.compare_digest(
                first.subject_kind.encode("utf-8"),
                second.subject_kind.encode("utf-8"),
            )
            and hmac.compare_digest(first_value, second_value)
        )

    def rotate(self, protected: ProtectedProviderId) -> ProtectedProviderId:
        return self.seal(protected.subject_kind, self.unseal(protected))
