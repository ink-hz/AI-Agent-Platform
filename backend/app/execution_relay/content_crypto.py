from __future__ import annotations

from dataclasses import dataclass, field
import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.control_plane.crypto import IdentityCryptoError, IdentityKeyring


class ContentCryptoError(RuntimeError):
    """Stable content-protection error that contains no protected values."""


@dataclass(frozen=True)
class SealedContent:
    ciphertext: bytes = field(repr=False)
    key_version: int

    def __repr__(self) -> str:
        return (
            "SealedContent(ciphertext=<redacted>, "
            f"key_version={self.key_version!r})"
        )


def _subject_bytes(subject: str) -> bytes:
    if not isinstance(subject, str) or not subject or "\0" in subject:
        raise ValueError
    return subject.encode("utf-8")


class ContentCodec:
    def __init__(self, keyring: IdentityKeyring) -> None:
        if (
            not isinstance(keyring, IdentityKeyring)
            or keyring.purpose != "platform-content-encryption"
            or keyring.key_lengths != {32}
            or keyring.transition_versions is not None
        ):
            raise ContentCryptoError("content keyring invalid")
        self._keyring = keyring

    def __repr__(self) -> str:
        return "ContentCodec(keyring=<redacted>)"

    @staticmethod
    def _aad(subject: str, version: int) -> bytes:
        normalized = _subject_bytes(subject)
        return b"orbbec-platform:" + normalized + f":v{version}".encode()

    def seal_json(
        self, subject: str, value: dict[str, object]
    ) -> SealedContent:
        try:
            if not isinstance(value, dict):
                raise ValueError
            version = self._keyring.active_version
            plaintext = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            nonce = secrets.token_bytes(12)
            encrypted = AESGCM(self._keyring.active_key).encrypt(
                nonce,
                plaintext,
                self._aad(subject, version),
            )
            return SealedContent(nonce + encrypted, version)
        except (
            IdentityCryptoError,
            OSError,
            TypeError,
            ValueError,
            UnicodeError,
        ):
            raise ContentCryptoError("content encrypt failed") from None

    def unseal_json(
        self, subject: str, sealed: SealedContent
    ) -> dict[str, object]:
        try:
            if (
                not isinstance(sealed, SealedContent)
                or not isinstance(sealed.ciphertext, bytes)
                or len(sealed.ciphertext) < 28
                or isinstance(sealed.key_version, bool)
                or not isinstance(sealed.key_version, int)
                or sealed.key_version <= 0
            ):
                raise ValueError
            key = self._keyring.key_for_version(sealed.key_version)
            plaintext = AESGCM(key).decrypt(
                sealed.ciphertext[:12],
                sealed.ciphertext[12:],
                self._aad(subject, sealed.key_version),
            )
            value = json.loads(plaintext.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (
            AttributeError,
            IdentityCryptoError,
            InvalidTag,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            UnicodeError,
        ):
            raise ContentCryptoError("content decrypt failed") from None
