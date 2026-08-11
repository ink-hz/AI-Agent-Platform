from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
import os
import shutil
import struct
import tempfile
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)


BACKUP_MAGIC = b"ORBBEC-REPLICA-BACKUP\n"
_HEADER_KEYS = {
    "version",
    "created_at",
    "encrypted_size",
    "plaintext_size",
    "plaintext_sha256",
    "ephemeral_public_key",
    "chunk_size",
}


class ReplicaBackupError(RuntimeError):
    """Stable, content-free backup boundary error."""


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    version: int
    created_at: datetime
    encrypted_size: int
    plaintext_size: int
    plaintext_sha256: str


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _derive_key(
    private_key: X25519PrivateKey,
    peer_public: X25519PublicKey,
    ephemeral_public: bytes,
    recovery_public: bytes,
) -> bytes:
    shared = private_key.exchange(peer_public)
    salt = hashlib.sha256(ephemeral_public + recovery_public).digest()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"orbbec-platform-replica-backup-v1",
    ).derive(shared)


def _chunk_aad(ephemeral_public: bytes, index: int) -> bytes:
    return b"orbbec-replica-backup:v1:chunk:" + index.to_bytes(8, "big") + ephemeral_public


def _read_exact(source: BinaryIO, size: int) -> bytes:
    value = source.read(size)
    if not isinstance(value, bytes) or len(value) != size:
        raise ValueError
    return value


def encrypt_stream(
    source: BinaryIO,
    target: BinaryIO,
    recovery_public_key: bytes,
    *,
    created_at: datetime | None = None,
    chunk_size: int = 1024 * 1024,
) -> BackupMetadata:
    try:
        if len(recovery_public_key) != 32 or not 4096 <= chunk_size <= 4 * 1024 * 1024:
            raise ValueError
        recovery_public = X25519PublicKey.from_public_bytes(recovery_public_key)
        ephemeral_private = X25519PrivateKey.generate()
        ephemeral_public = ephemeral_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        key = _derive_key(
            ephemeral_private,
            recovery_public,
            ephemeral_public,
            recovery_public_key,
        )
        cipher = AESGCM(key)
        plaintext_hash = hashlib.sha256()
        plaintext_size = 0
        encrypted_size = 0
        with tempfile.TemporaryFile(mode="w+b") as encrypted_body:
            index = 0
            while True:
                chunk = source.read(chunk_size)
                if chunk == b"":
                    break
                if not isinstance(chunk, bytes) or len(chunk) > chunk_size:
                    raise ValueError
                plaintext_hash.update(chunk)
                plaintext_size += len(chunk)
                nonce = os.urandom(12)
                ciphertext = cipher.encrypt(
                    nonce, chunk, _chunk_aad(ephemeral_public, index)
                )
                record = nonce + ciphertext
                encrypted_body.write(struct.pack(">I", len(record)))
                encrypted_body.write(record)
                encrypted_size += 4 + len(record)
                index += 1
            created = created_at or datetime.now(UTC)
            header = {
                "version": 1,
                "created_at": _timestamp(created),
                "encrypted_size": encrypted_size,
                "plaintext_size": plaintext_size,
                "plaintext_sha256": plaintext_hash.hexdigest(),
                "ephemeral_public_key": ephemeral_public.hex(),
                "chunk_size": chunk_size,
            }
            header_bytes = _canonical(header)
            header_nonce = os.urandom(12)
            header_tag = cipher.encrypt(header_nonce, b"", header_bytes)
            target.write(BACKUP_MAGIC)
            target.write(struct.pack(">I", len(header_bytes)))
            target.write(header_bytes)
            target.write(header_nonce)
            target.write(header_tag)
            encrypted_body.seek(0)
            shutil.copyfileobj(encrypted_body, target, length=1024 * 1024)
        return BackupMetadata(
            version=1,
            created_at=created.astimezone(UTC),
            encrypted_size=encrypted_size,
            plaintext_size=plaintext_size,
            plaintext_sha256=plaintext_hash.hexdigest(),
        )
    except ReplicaBackupError:
        raise
    except Exception:
        raise ReplicaBackupError("backup_failed") from None


def decrypt_stream(
    source: BinaryIO,
    target: BinaryIO,
    recovery_private_key: bytes,
) -> BackupMetadata:
    try:
        if len(recovery_private_key) != 32:
            raise ValueError
        if _read_exact(source, len(BACKUP_MAGIC)) != BACKUP_MAGIC:
            raise ValueError
        header_length = struct.unpack(">I", _read_exact(source, 4))[0]
        if not 1 <= header_length <= 4096:
            raise ValueError
        header_bytes = _read_exact(source, header_length)
        header = json.loads(header_bytes)
        if not isinstance(header, dict) or set(header) != _HEADER_KEYS:
            raise ValueError
        if (
            header["version"] != 1
            or type(header["encrypted_size"]) is not int
            or header["encrypted_size"] < 0
            or type(header["plaintext_size"]) is not int
            or header["plaintext_size"] < 0
            or type(header["chunk_size"]) is not int
            or not 4096 <= header["chunk_size"] <= 4 * 1024 * 1024
            or not isinstance(header["plaintext_sha256"], str)
            or len(header["plaintext_sha256"]) != 64
        ):
            raise ValueError
        ephemeral_public_bytes = bytes.fromhex(header["ephemeral_public_key"])
        if len(ephemeral_public_bytes) != 32:
            raise ValueError
        ephemeral_public = X25519PublicKey.from_public_bytes(
            ephemeral_public_bytes
        )
        recovery_private = X25519PrivateKey.from_private_bytes(
            recovery_private_key
        )
        recovery_public_bytes = recovery_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        key = _derive_key(
            recovery_private,
            ephemeral_public,
            ephemeral_public_bytes,
            recovery_public_bytes,
        )
        cipher = AESGCM(key)
        header_nonce = _read_exact(source, 12)
        header_tag = _read_exact(source, 16)
        cipher.decrypt(header_nonce, header_tag, header_bytes)
        remaining = header["encrypted_size"]
        plaintext_hash = hashlib.sha256()
        plaintext_size = 0
        index = 0
        while remaining:
            if remaining < 4:
                raise ValueError
            record_length = struct.unpack(">I", _read_exact(source, 4))[0]
            remaining -= 4
            if not 28 <= record_length <= header["chunk_size"] + 28 or record_length > remaining:
                raise ValueError
            record = _read_exact(source, record_length)
            remaining -= record_length
            plaintext = cipher.decrypt(
                record[:12],
                record[12:],
                _chunk_aad(ephemeral_public_bytes, index),
            )
            plaintext_hash.update(plaintext)
            plaintext_size += len(plaintext)
            target.write(plaintext)
            index += 1
        if source.read(1) != b"":
            raise ValueError
        if plaintext_size != header["plaintext_size"] or not hmac.compare_digest(
            plaintext_hash.hexdigest(), header["plaintext_sha256"]
        ):
            raise ValueError
        created = datetime.fromisoformat(
            header["created_at"].replace("Z", "+00:00")
        ).astimezone(UTC)
        return BackupMetadata(
            version=1,
            created_at=created,
            encrypted_size=header["encrypted_size"],
            plaintext_size=plaintext_size,
            plaintext_sha256=plaintext_hash.hexdigest(),
        )
    except (InvalidTag, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ReplicaBackupError("restore_failed") from None
    except Exception:
        raise ReplicaBackupError("restore_failed") from None
