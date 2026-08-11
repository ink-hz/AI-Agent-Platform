from datetime import UTC, datetime
import io
import json
import struct

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from app.cloud_replica.backup import (
    BACKUP_MAGIC,
    ReplicaBackupError,
    decrypt_stream,
    encrypt_stream,
)


def _keys():
    private = X25519PrivateKey.generate()
    return (
        private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )


def _header(payload: bytes):
    assert payload.startswith(BACKUP_MAGIC)
    offset = len(BACKUP_MAGIC)
    length = struct.unpack(">I", payload[offset : offset + 4])[0]
    return json.loads(payload[offset + 4 : offset + 4 + length])


def test_backup_encrypts_dump_without_plaintext_and_matching_key_restores():
    private, public = _keys()
    plaintext = (
        "synthetic-question=客户甲\n"
        "employee=磐德\n"
        "dsn=postgresql://user:password@host/db\n"
    ).encode() * 5000
    encrypted = io.BytesIO()

    metadata = encrypt_stream(
        io.BytesIO(plaintext),
        encrypted,
        public,
        created_at=datetime(2026, 8, 11, 8, 0, tzinfo=UTC),
        chunk_size=4096,
    )
    payload = encrypted.getvalue()
    restored = io.BytesIO()
    decrypt_stream(io.BytesIO(payload), restored, private)

    assert restored.getvalue() == plaintext
    assert plaintext not in payload
    for canary in (b"synthetic-question", "客户甲".encode(), b"postgresql://", b"password"):
        assert canary not in payload
    header = _header(payload)
    assert header["version"] == 1
    assert header["created_at"] == "2026-08-11T08:00:00.000000Z"
    assert header["encrypted_size"] == metadata.encrypted_size
    assert header["plaintext_sha256"] == metadata.plaintext_sha256
    assert header["ephemeral_public_key"]


def test_wrong_recovery_key_and_tampering_fail_without_plaintext_in_error():
    private, public = _keys()
    wrong_private, _ = _keys()
    encrypted = io.BytesIO()
    encrypt_stream(io.BytesIO(b"do-not-echo"), encrypted, public)

    with pytest.raises(ReplicaBackupError, match="restore_failed") as error:
        decrypt_stream(io.BytesIO(encrypted.getvalue()), io.BytesIO(), wrong_private)
    assert "do-not-echo" not in str(error.value)

    tampered = bytearray(encrypted.getvalue())
    tampered[-1] ^= 1
    with pytest.raises(ReplicaBackupError, match="restore_failed"):
        decrypt_stream(io.BytesIO(bytes(tampered)), io.BytesIO(), private)


def test_backup_rejects_bad_key_sizes_and_invalid_chunk_size():
    with pytest.raises(ReplicaBackupError, match="backup_failed"):
        encrypt_stream(io.BytesIO(b"x"), io.BytesIO(), b"short")
    _, public = _keys()
    with pytest.raises(ReplicaBackupError, match="backup_failed"):
        encrypt_stream(io.BytesIO(b"x"), io.BytesIO(), public, chunk_size=0)
