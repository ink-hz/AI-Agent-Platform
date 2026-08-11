import base64
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.cloud_replica.crypto import BatchSigner, BatchVerifier
from app.cloud_replica.protocol import (
    BatchLimits,
    BatchState,
    ReplicaProtocolError,
    decode_and_verify_batch,
    encode_batch,
)


def _keys():
    private = Ed25519PrivateKey.generate()
    return BatchSigner(private), BatchVerifier(private.public_key())


def _state(**changes) -> BatchState:
    created = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    values = {
        "source_instance_id": "local-platform-1",
        "sequence": 1,
        "previous_digest": None,
        "lower_watermark": created - timedelta(minutes=5),
        "upper_watermark": created,
        "created_at": created,
        "expires_at": created + timedelta(minutes=15),
        "sanitizer_policy_version": "test-v1",
    }
    values.update(changes)
    return BatchState(**values)


def _records():
    return (
        {"kind": "session", "key": "s1", "title": "安全内容"},
        {"kind": "turn", "key": "t1", "question": "已脱敏"},
    )


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _resign(lines: list[bytes], signer: BatchSigner) -> bytes:
    content = b"\n".join(lines[:-1]) + b"\n"
    digest = hashlib.sha256(content).hexdigest()
    trailer = {
        "digest": digest,
        "signature": base64.urlsafe_b64encode(signer.sign(digest.encode("ascii")))
        .rstrip(b"=")
        .decode("ascii"),
    }
    return content + _canonical(trailer) + b"\n"


def test_batch_encoding_is_canonical_deterministic_and_verified():
    signer, verifier = _keys()

    first = encode_batch(_records(), _state(), signer)
    second = encode_batch(_records(), _state(), signer)
    decoded = decode_and_verify_batch(io.BytesIO(first), verifier, BatchLimits())

    assert first == second
    assert decoded.header.sequence == 1
    assert decoded.header.record_count == 2
    assert decoded.records == _records()
    assert decoded.digest == hashlib.sha256(b"\n".join(first.splitlines()[:-1]) + b"\n").hexdigest()


@pytest.mark.parametrize("line_index", [0, 1, -1])
def test_mutated_content_header_or_signature_is_rejected(line_index):
    signer, verifier = _keys()
    lines = encode_batch(_records(), _state(), signer).splitlines()
    mutated = bytearray(lines[line_index])
    mutated[len(mutated) // 2] ^= 1
    lines[line_index] = bytes(mutated)

    with pytest.raises(ReplicaProtocolError, match="batch_invalid"):
        decode_and_verify_batch(io.BytesIO(b"\n".join(lines) + b"\n"), verifier, BatchLimits())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sequence", 0),
        ("sequence", 2),
        ("previous_digest", "bad"),
        ("created_at", "not-a-time"),
        ("expires_at", "2020-01-01T00:00:00Z"),
        ("schema_version", 99),
        ("record_count", 3),
        ("records_byte_count", 1),
    ],
)
def test_resigned_invalid_header_is_rejected(field, value):
    signer, verifier = _keys()
    lines = encode_batch(_records(), _state(), signer).splitlines()
    header = json.loads(lines[0])
    header[field] = value
    lines[0] = _canonical(header)

    with pytest.raises(ReplicaProtocolError, match="batch_invalid"):
        decode_and_verify_batch(io.BytesIO(_resign(lines, signer)), verifier, BatchLimits())


def test_sequence_two_requires_exact_predecessor_digest():
    signer, verifier = _keys()
    state = _state(sequence=2, previous_digest="a" * 64)

    decoded = decode_and_verify_batch(
        io.BytesIO(encode_batch(_records(), state, signer)), verifier, BatchLimits()
    )

    assert decoded.header.previous_digest == "a" * 64


def test_batch_record_and_stream_limits_are_enforced():
    signer, verifier = _keys()
    record = {"kind": "turn", "key": "t1", "value": "x" * 200}
    payload = encode_batch((record,), _state(), signer)

    with pytest.raises(ReplicaProtocolError, match="batch_invalid"):
        decode_and_verify_batch(
            io.BytesIO(payload), verifier, BatchLimits(max_batch_bytes=100)
        )
    with pytest.raises(ReplicaProtocolError, match="batch_invalid"):
        decode_and_verify_batch(
            io.BytesIO(payload), verifier, BatchLimits(max_record_bytes=100)
        )
    with pytest.raises(ReplicaProtocolError, match="batch_invalid"):
        decode_and_verify_batch(
            io.BytesIO(payload), verifier, BatchLimits(max_records=0)
        )


def test_default_limits_match_security_contract():
    limits = BatchLimits()

    assert limits.max_batch_bytes == 10 * 1024 * 1024
    assert limits.max_record_bytes == 1024 * 1024
    assert limits.max_records == 10_000
