from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import binascii
import hashlib
import hmac
import re
import secrets
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn


_BODY_LIMIT = 1_048_576
_TIMESTAMP_WINDOW = timedelta(seconds=60)
_WORKER_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_KEY_ID = re.compile(r"worker-v[1-9][0-9]*\Z")
_TIMESTAMP = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_HEADER_WORKER_ID = "X-Orbbec-Worker-Id"
_HEADER_KEY_ID = "X-Orbbec-Worker-Key-Id"
_HEADER_TIMESTAMP = "X-Orbbec-Worker-Timestamp"
_HEADER_NONCE = "X-Orbbec-Worker-Nonce"
_HEADER_SIGNATURE = "X-Orbbec-Worker-Signature"
_REQUIRED_HEADERS = (
    _HEADER_WORKER_ID,
    _HEADER_KEY_ID,
    _HEADER_TIMESTAMP,
    _HEADER_NONCE,
    _HEADER_SIGNATURE,
)


class WorkerAuthenticationError(RuntimeError):
    """Stable request-authentication failure without protected values."""

    def __init__(self) -> None:
        super().__init__("worker authentication failed")


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    key_id: str
    allowed_agent_ids: tuple[str, ...]


def _utc_datetime(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError
    if current.utcoffset() is None:
        raise ValueError
    return current.astimezone(timezone.utc)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str, *, expected_size: int) -> bytes:
    if (
        not isinstance(value, str)
        or _BASE64URL.fullmatch(value) is None
        or len(value) != (43 if expected_size == 32 else 86)
    ):
        raise ValueError
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
    if len(decoded) != expected_size:
        raise ValueError
    if not hmac.compare_digest(_base64url_encode(decoded), value):
        raise ValueError
    return decoded


def _canonical_request(
    method: str,
    path_with_query: str,
    body: bytes,
    timestamp: str,
    nonce: str,
) -> bytes:
    if not isinstance(method, str) or not isinstance(path_with_query, str):
        raise ValueError
    body_digest = hashlib.sha256(body).hexdigest()
    return (
        "orbbec-agent-worker-v1\n"
        f"{method}\n"
        f"{path_with_query}\n"
        f"{timestamp}\n"
        f"{nonce}\n"
        f"{body_digest}"
    ).encode("utf-8")


def _required_header_values(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        raise ValueError
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError
        lowered = name.lower()
        if lowered in normalized:
            raise ValueError
        normalized[lowered] = value
    values: dict[str, str] = {}
    for name in _REQUIRED_HEADERS:
        value = normalized.get(name.lower())
        if value is None or not value:
            raise ValueError
        values[name] = value
    return values


class WorkerRequestSigner:
    def __init__(
        self,
        worker_id: str,
        key_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        if (
            not isinstance(worker_id, str)
            or _WORKER_ID.fullmatch(worker_id) is None
            or not isinstance(key_id, str)
            or _KEY_ID.fullmatch(key_id) is None
            or not isinstance(private_key, Ed25519PrivateKey)
        ):
            raise ValueError("worker signer invalid")
        self.worker_id = worker_id
        self.key_id = key_id
        self._private_key = private_key

    def __repr__(self) -> str:
        return (
            f"WorkerRequestSigner(worker_id={self.worker_id!r}, "
            f"key_id={self.key_id!r}, private_key=<redacted>)"
        )

    def sign(
        self,
        method: str,
        path_with_query: str,
        body: bytes,
        *,
        now: datetime | None = None,
    ) -> dict[str, str]:
        if not isinstance(body, bytes):
            raise ValueError("worker signing request invalid")
        timestamp_value = int(_utc_datetime(now).timestamp())
        if timestamp_value < 0:
            raise ValueError("worker signing request invalid")
        timestamp = str(timestamp_value)
        nonce_bytes = secrets.token_bytes(32)
        if len(nonce_bytes) != 32:
            raise ValueError("worker signing request invalid")
        nonce = _base64url_encode(nonce_bytes)
        canonical = _canonical_request(
            method, path_with_query, body, timestamp, nonce
        )
        signature = _base64url_encode(self._private_key.sign(canonical))
        return {
            _HEADER_WORKER_ID: self.worker_id,
            _HEADER_KEY_ID: self.key_id,
            _HEADER_TIMESTAMP: timestamp,
            _HEADER_NONCE: nonce,
            _HEADER_SIGNATURE: signature,
        }


class WorkerRequestVerifier:
    def __init__(
        self,
        control_database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose="app")
        self._control_database_url = control_database_url
        self._connect = connect

    def __repr__(self) -> str:
        return "WorkerRequestVerifier(control_database_url=<redacted>)"

    def _connection(self):
        return self._connect(
            self._control_database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def verify(
        self,
        method: str,
        path_with_query: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        now: datetime | None = None,
    ) -> WorkerIdentity:
        try:
            if not isinstance(body, bytes) or len(body) > _BODY_LIMIT:
                raise ValueError
            current = _utc_datetime(now)
            values = _required_header_values(headers)
            worker_id = values[_HEADER_WORKER_ID]
            key_id = values[_HEADER_KEY_ID]
            timestamp_text = values[_HEADER_TIMESTAMP]
            nonce_text = values[_HEADER_NONCE]
            signature_text = values[_HEADER_SIGNATURE]
            if (
                _WORKER_ID.fullmatch(worker_id) is None
                or _KEY_ID.fullmatch(key_id) is None
                or _TIMESTAMP.fullmatch(timestamp_text) is None
            ):
                raise ValueError
            timestamp = int(timestamp_text)
            if abs(current.timestamp() - timestamp) > 60:
                raise ValueError
            nonce = _base64url_decode(nonce_text, expected_size=32)
            signature = _base64url_decode(
                signature_text, expected_size=64
            )
            canonical = _canonical_request(
                method, path_with_query, body, timestamp_text, nonce_text
            )
            identity: WorkerIdentity | None = None
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select platform_control.touch_execution_worker_v27(%s)",
                    (worker_id,),
                )
                row = cursor.execute(
                    "select worker.allowed_agent_ids,key.public_key "
                    "from platform_control.execution_workers worker "
                    "join platform_control.execution_worker_keys key "
                    "on key.worker_id=worker.worker_id "
                    "where worker.worker_id=%s and worker.status='active' "
                    "and key.key_id=%s and key.status='active'",
                    (worker_id, key_id),
                ).fetchone()
                if row is None:
                    raise WorkerAuthenticationError()
                public_key = Ed25519PublicKey.from_public_bytes(
                    bytes(row["public_key"])
                )
                public_key.verify(signature, canonical)
                cursor.execute(
                    "delete from platform_control.execution_worker_nonces "
                    "where worker_id=%s and expires_at < %s",
                    (worker_id, current),
                )
                inserted = cursor.execute(
                    "insert into platform_control.execution_worker_nonces "
                    "(worker_id,nonce,expires_at) values (%s,%s,%s) "
                    "on conflict (worker_id,nonce) do nothing returning nonce",
                    (
                        worker_id,
                        nonce,
                        datetime.fromtimestamp(timestamp, timezone.utc)
                        + _TIMESTAMP_WINDOW,
                    ),
                ).fetchone()
                if inserted is None:
                    raise WorkerAuthenticationError()
                identity = WorkerIdentity(
                    worker_id=worker_id,
                    key_id=key_id,
                    allowed_agent_ids=tuple(row["allowed_agent_ids"]),
                )
            if identity is None:
                raise WorkerAuthenticationError()
            return identity
        except WorkerAuthenticationError:
            raise
        except (
            InvalidSignature,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            binascii.Error,
            OverflowError,
            OSError,
            psycopg.Error,
        ):
            raise WorkerAuthenticationError() from None
