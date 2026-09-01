from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
from io import BufferedIOBase
import json
import re
from typing import Any, BinaryIO

from .crypto import BatchSigner, BatchVerifier, ReplicaCryptoError


PROTOCOL_VERSION = 1
SCHEMA_VERSION = 3


class ReplicaProtocolError(RuntimeError):
    """Stable, record-free protocol rejection."""


@dataclass(frozen=True, slots=True)
class BatchLimits:
    max_batch_bytes: int = 10 * 1024 * 1024
    max_record_bytes: int = 1024 * 1024
    max_records: int = 10_000


@dataclass(frozen=True, slots=True)
class BatchState:
    source_instance_id: str
    sequence: int
    previous_digest: str | None
    lower_watermark: datetime
    upper_watermark: datetime
    created_at: datetime
    expires_at: datetime
    sanitizer_policy_version: str


@dataclass(frozen=True, slots=True)
class BatchHeader:
    protocol_version: int
    schema_version: int
    sanitizer_policy_version: str
    source_instance_id: str
    sequence: int
    previous_digest: str | None
    lower_watermark: datetime
    upper_watermark: datetime
    created_at: datetime
    expires_at: datetime
    record_count: int
    records_byte_count: int
    record_counts: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class SignedBatch:
    header: BatchHeader
    records: tuple[dict[str, Any], ...]
    digest: str


_HEADER_KEYS = {
    "protocol_version",
    "schema_version",
    "sanitizer_policy_version",
    "source_instance_id",
    "sequence",
    "previous_digest",
    "lower_watermark",
    "upper_watermark",
    "created_at",
    "expires_at",
    "record_count",
    "records_byte_count",
}
_HEADER_V2_KEYS = _HEADER_KEYS | {"record_counts"}
_SOURCE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ReplicaProtocolError("batch_invalid") from None


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ReplicaProtocolError("batch_invalid")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError
    return parsed.astimezone(UTC)


def _header_from_dict(value: dict[str, Any]) -> BatchHeader:
    keys = set(value)
    if keys != _HEADER_KEYS and keys != _HEADER_V2_KEYS:
        raise ValueError
    header = BatchHeader(
        protocol_version=value["protocol_version"],
        schema_version=value["schema_version"],
        sanitizer_policy_version=value["sanitizer_policy_version"],
        source_instance_id=value["source_instance_id"],
        sequence=value["sequence"],
        previous_digest=value["previous_digest"],
        lower_watermark=_parse_timestamp(value["lower_watermark"]),
        upper_watermark=_parse_timestamp(value["upper_watermark"]),
        created_at=_parse_timestamp(value["created_at"]),
        expires_at=_parse_timestamp(value["expires_at"]),
        record_count=value["record_count"],
        records_byte_count=value["records_byte_count"],
        record_counts=value.get("record_counts"),
    )
    if (
        type(header.protocol_version) is not int
        or header.protocol_version != PROTOCOL_VERSION
        or type(header.schema_version) is not int
        or header.schema_version not in {1, 2, SCHEMA_VERSION}
        or not isinstance(header.sanitizer_policy_version, str)
        or not 1 <= len(header.sanitizer_policy_version) <= 64
        or not isinstance(header.source_instance_id, str)
        or not _SOURCE_ID.fullmatch(header.source_instance_id)
        or type(header.sequence) is not int
        or header.sequence < 1
        or type(header.record_count) is not int
        or header.record_count < 0
        or type(header.records_byte_count) is not int
        or header.records_byte_count < 0
        or not (
            header.lower_watermark
            <= header.upper_watermark
            <= header.created_at
            < header.expires_at
        )
    ):
        raise ValueError
    if header.schema_version == 1:
        if header.record_counts is not None:
            raise ValueError
    elif (
        not isinstance(header.record_counts, dict)
        or any(
            not isinstance(kind, str)
            or not kind
            or type(count) is not int
            or count < 0
            for kind, count in header.record_counts.items()
        )
        or sum(header.record_counts.values()) != header.record_count
    ):
        raise ValueError
    if header.sequence == 1:
        if header.previous_digest is not None:
            raise ValueError
    elif not isinstance(header.previous_digest, str) or not _DIGEST.fullmatch(
        header.previous_digest
    ):
        raise ValueError
    return header


def _header_dict(
    state: BatchState,
    record_count: int,
    records_byte_count: int,
    record_counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "sanitizer_policy_version": state.sanitizer_policy_version,
        "source_instance_id": state.source_instance_id,
        "sequence": state.sequence,
        "previous_digest": state.previous_digest,
        "lower_watermark": _timestamp(state.lower_watermark),
        "upper_watermark": _timestamp(state.upper_watermark),
        "created_at": _timestamp(state.created_at),
        "expires_at": _timestamp(state.expires_at),
        "record_count": record_count,
        "records_byte_count": records_byte_count,
        "record_counts": record_counts,
    }


def encode_batch(
    records: tuple[dict[str, Any], ...],
    state: BatchState,
    signer: BatchSigner,
) -> bytes:
    try:
        record_lines = tuple(_canonical(record) for record in records)
        record_counts: dict[str, int] = {}
        for record in records:
            kind = record.get("kind")
            if not isinstance(kind, str) or not kind:
                raise ReplicaProtocolError("batch_invalid")
            record_counts[kind] = record_counts.get(kind, 0) + 1
        records_byte_count = sum(len(line) + 1 for line in record_lines)
        header_value = _header_dict(
            state, len(records), records_byte_count, record_counts
        )
        _header_from_dict(header_value)
        content = b"\n".join((_canonical(header_value), *record_lines)) + b"\n"
        digest = hashlib.sha256(content).hexdigest()
        signature = base64.urlsafe_b64encode(
            signer.sign(digest.encode("ascii"))
        ).rstrip(b"=").decode("ascii")
        trailer = _canonical({"digest": digest, "signature": signature})
        return content + trailer + b"\n"
    except ReplicaProtocolError:
        raise
    except Exception:
        raise ReplicaProtocolError("batch_invalid") from None


def _decode_json_line(line: bytes) -> dict[str, Any]:
    value = json.loads(line.decode("utf-8"))
    if not isinstance(value, dict) or _canonical(value) != line:
        raise ValueError
    return value


def decode_and_verify_batch(
    stream: BinaryIO | BufferedIOBase,
    verifier: BatchVerifier,
    limits: BatchLimits,
) -> SignedBatch:
    try:
        if (
            limits.max_batch_bytes <= 0
            or limits.max_record_bytes <= 0
            or limits.max_records <= 0
        ):
            raise ValueError
        payload = stream.read(limits.max_batch_bytes + 1)
        if (
            not isinstance(payload, bytes)
            or len(payload) > limits.max_batch_bytes
            or not payload.endswith(b"\n")
        ):
            raise ValueError
        lines = payload.splitlines()
        if len(lines) < 2 or any(not line for line in lines):
            raise ValueError
        header_value = _decode_json_line(lines[0])
        trailer = _decode_json_line(lines[-1])
        if set(trailer) != {"digest", "signature"}:
            raise ValueError
        header = _header_from_dict(header_value)
        record_lines = lines[1:-1]
        if len(record_lines) > limits.max_records or any(
            len(line) > limits.max_record_bytes for line in record_lines
        ):
            raise ValueError
        if header.record_count != len(record_lines) or header.records_byte_count != sum(
            len(line) + 1 for line in record_lines
        ):
            raise ValueError
        records = tuple(_decode_json_line(line) for line in record_lines)
        if header.record_counts is not None:
            actual_counts: dict[str, int] = {}
            for record in records:
                kind = record.get("kind")
                if not isinstance(kind, str) or not kind:
                    raise ValueError
                actual_counts[kind] = actual_counts.get(kind, 0) + 1
            if actual_counts != header.record_counts:
                raise ValueError
        content = b"\n".join(lines[:-1]) + b"\n"
        digest = hashlib.sha256(content).hexdigest()
        if not isinstance(trailer["digest"], str) or not _DIGEST.fullmatch(
            trailer["digest"]
        ) or not hmac.compare_digest(digest, trailer["digest"]):
            raise ValueError
        signature_value = trailer["signature"]
        if not isinstance(signature_value, str):
            raise ValueError
        signature = base64.b64decode(
            signature_value + "=" * (-len(signature_value) % 4),
            altchars=b"-_",
            validate=True,
        )
        verifier.verify(digest.encode("ascii"), signature)
        return SignedBatch(header=header, records=records, digest=digest)
    except (
        ReplicaCryptoError,
        ReplicaProtocolError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise ReplicaProtocolError("batch_invalid") from None
