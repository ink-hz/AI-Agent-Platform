from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from pathlib import Path
import stat
from typing import Mapping

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class ReplicaCryptoError(RuntimeError):
    """Stable, payload-free cryptographic boundary error."""


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        value + padding, altchars=b"-_", validate=True
    )


def read_key_file(path_value: str | Path, *, expected_size: int) -> bytes:
    path = Path(path_value)
    if not path.is_absolute():
        raise ReplicaCryptoError("key_file_must_be_absolute")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReplicaCryptoError("key_file_must_be_regular") from error
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ReplicaCryptoError("key_file_must_be_regular")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ReplicaCryptoError("key_file_must_use_0600")
    if metadata.st_uid != os.getuid():
        raise ReplicaCryptoError("key_file_wrong_owner")
    try:
        value = path.read_bytes()
    except OSError as error:
        raise ReplicaCryptoError("key_file_read_failed") from error
    if len(value) != expected_size:
        raise ReplicaCryptoError("key_file_wrong_size")
    return value


def stable_id(scope: str, value: str, key: bytes) -> str:
    try:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError
        if not scope or not value or "\0" in scope:
            raise ValueError
        digest = hmac.digest(
            key, f"{scope}\0{value}".encode("utf-8"), hashlib.sha256
        )
        return base64.b32encode(digest).rstrip(b"=").decode("ascii").lower()
    except (TypeError, ValueError, UnicodeError):
        raise ReplicaCryptoError("identity_failed") from None


class FieldCipher:
    def __init__(self, key: bytes):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ReplicaCryptoError("encryption_key_invalid")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str, associated_data: str) -> dict[str, str]:
        try:
            nonce = os.urandom(12)
            ciphertext = self._cipher.encrypt(
                nonce,
                plaintext.encode("utf-8"),
                associated_data.encode("utf-8"),
            )
            return {
                "nonce": _b64_encode(nonce),
                "ciphertext": _b64_encode(ciphertext),
            }
        except (TypeError, ValueError, UnicodeError):
            raise ReplicaCryptoError("encrypt_failed") from None

    def decrypt(
        self, encrypted: Mapping[str, str], associated_data: str
    ) -> str:
        try:
            if set(encrypted) != {"nonce", "ciphertext"}:
                raise ValueError
            nonce = _b64_decode(encrypted["nonce"])
            if len(nonce) != 12:
                raise ValueError
            plaintext = self._cipher.decrypt(
                nonce,
                _b64_decode(encrypted["ciphertext"]),
                associated_data.encode("utf-8"),
            )
            return plaintext.decode("utf-8")
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            binascii.Error,
            InvalidTag,
        ):
            raise ReplicaCryptoError("decrypt_failed") from None


class BatchSigner:
    def __init__(self, private_key: Ed25519PrivateKey):
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ReplicaCryptoError("signing_key_invalid")
        self._private_key = private_key

    @classmethod
    def from_private_key_file(cls, path: str | Path) -> BatchSigner:
        try:
            key = read_key_file(path, expected_size=32)
            return cls(Ed25519PrivateKey.from_private_bytes(key))
        except ReplicaCryptoError:
            raise
        except (TypeError, ValueError):
            raise ReplicaCryptoError("signing_key_invalid") from None

    def sign(self, content: bytes) -> bytes:
        try:
            return self._private_key.sign(content)
        except (TypeError, ValueError):
            raise ReplicaCryptoError("signature_failed") from None


class BatchVerifier:
    def __init__(self, public_key: Ed25519PublicKey):
        if not isinstance(public_key, Ed25519PublicKey):
            raise ReplicaCryptoError("signing_key_invalid")
        self._public_key = public_key

    @classmethod
    def from_public_key_file(cls, path: str | Path) -> BatchVerifier:
        try:
            key = read_key_file(path, expected_size=32)
            return cls(Ed25519PublicKey.from_public_bytes(key))
        except ReplicaCryptoError:
            raise
        except (TypeError, ValueError):
            raise ReplicaCryptoError("signing_key_invalid") from None

    def verify(self, content: bytes, signature: bytes) -> None:
        try:
            self._public_key.verify(signature, content)
        except (InvalidSignature, TypeError, ValueError):
            raise ReplicaCryptoError("signature_failed") from None
