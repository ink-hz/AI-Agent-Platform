"""Short-lived Ed25519 identity tokens for internal professional-Agent tasks."""

from __future__ import annotations

import base64
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if type(value) is not str or not value:
        raise ValueError("Task token invalid")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError):
        raise ValueError("Task token invalid") from None


def _json_segment(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _base64url_encode(encoded)


def _utc(value: datetime, *, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"Task {name} must be UTC")
    normalized = value.astimezone(timezone.utc)
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"Task {name} must be UTC")
    return normalized


def _rfc3339_seconds(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_private_key(path_value: str | Path) -> Ed25519PrivateKey:
    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeError("Task signing key must use an absolute path")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(
            "Task signing key must be a regular mode 0600 file"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Task signing key must be a regular mode 0600 file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise RuntimeError("Task signing key must use mode 0600")
        if metadata.st_uid != os.getuid():
            raise RuntimeError("Task signing key must be owned by the service user")
        raw = b""
        while len(raw) <= 32:
            chunk = os.read(descriptor, 33 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(descriptor)
    if len(raw) != 32:
        raise RuntimeError("Task signing key must contain 32 raw Ed25519 bytes")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as error:
        raise RuntimeError("Task signing key is invalid") from error


class SignedTaskTokenIssuer:
    """Issue task-, actor-, Agent-, capability-, scope-, and deadline-bound JWTs."""

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        *,
        kid: str,
        issuer: str = "orbbec-agent-platform",
        ttl_seconds: int = 60,
    ) -> None:
        if (
            not isinstance(private_key, Ed25519PrivateKey)
            or type(kid) is not str
            or _IDENTIFIER.fullmatch(kid) is None
            or type(issuer) is not str
            or _IDENTIFIER.fullmatch(issuer) is None
            or type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= 300
        ):
            raise ValueError("Task token issuer configuration invalid")
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self.kid = kid
        self.issuer = issuer
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        kid: str,
        issuer: str = "orbbec-agent-platform",
        ttl_seconds: int = 60,
    ) -> SignedTaskTokenIssuer:
        return cls(
            _read_private_key(path),
            kid=kid,
            issuer=issuer,
            ttl_seconds=ttl_seconds,
        )

    def issue(
        self,
        *,
        audience: str,
        internal_user_id: UUID,
        agent_id: str,
        agent_task_id: UUID,
        capability_version: int,
        authorized_scopes: Sequence[str],
        task_deadline_at: datetime,
        action_execution_deadline_at: datetime | None = None,
        now: datetime | None = None,
        request_id: UUID | None = None,
    ) -> str:
        if (
            type(audience) is not str
            or _IDENTIFIER.fullmatch(audience) is None
            or type(agent_id) is not str
            or _IDENTIFIER.fullmatch(agent_id) is None
            or not isinstance(internal_user_id, UUID)
            or not isinstance(agent_task_id, UUID)
            or type(capability_version) is not int
            or capability_version <= 0
        ):
            raise ValueError("Task token identity invalid")
        scopes = tuple(authorized_scopes)
        if (
            not scopes
            or any(
                type(scope) is not str or _IDENTIFIER.fullmatch(scope) is None
                for scope in scopes
            )
            or tuple(sorted(set(scopes))) != scopes
        ):
            raise ValueError("Task token scopes invalid")
        issued = (
            datetime.now(timezone.utc) if now is None else _utc(now, name="issue time")
        )
        task_deadline = _utc(task_deadline_at, name="deadline")
        action_deadline = (
            None
            if action_execution_deadline_at is None
            else _utc(action_execution_deadline_at, name="action deadline")
        )
        governing_deadline = action_deadline or task_deadline
        if governing_deadline <= issued:
            raise ValueError("Task deadline expired")
        token_id = uuid4() if request_id is None else request_id
        if not isinstance(token_id, UUID):
            raise ValueError("Task request ID invalid")
        expires_at = min(
            int(issued.timestamp()) + self.ttl_seconds,
            int(governing_deadline.timestamp()),
        )
        claims: dict[str, object] = {
            "iss": self.issuer,
            "aud": audience,
            "sub": str(internal_user_id),
            "internal_user_id": str(internal_user_id),
            "agent_id": agent_id,
            "agent_task_id": str(agent_task_id),
            "capability_version": capability_version,
            "authorized_scopes": list(scopes),
            "task_deadline_at": _rfc3339_seconds(task_deadline),
            "iat": int(issued.timestamp()),
            "exp": expires_at,
            "request_id": str(token_id),
        }
        if action_deadline is not None:
            claims["action_execution_deadline_at"] = _rfc3339_seconds(action_deadline)
        header = _json_segment({"alg": "EdDSA", "kid": self.kid, "typ": "JWT"})
        payload = _json_segment(claims)
        signing_input = f"{header}.{payload}".encode("ascii")
        signature = _base64url_encode(self._private_key.sign(signing_input))
        return f"{signing_input.decode('ascii')}.{signature}"

    def verify(
        self,
        token: str,
        *,
        audience: str,
        now: datetime | None = None,
        public_key: Ed25519PublicKey | None = None,
    ) -> dict[str, object]:
        """Verify tokens in tests and local contract tooling.

        Production Agent services maintain their own active/previous public-key
        keyring; they do not call back to the Platform issuer.
        """

        try:
            header_segment, payload_segment, signature_segment = token.split(".")
            header = json.loads(_base64url_decode(header_segment))
            claims = json.loads(_base64url_decode(payload_segment))
            if type(header) is not dict or type(claims) is not dict:
                raise ValueError
            if header != {"alg": "EdDSA", "kid": self.kid, "typ": "JWT"}:
                raise ValueError
            (public_key or self._public_key).verify(
                _base64url_decode(signature_segment),
                f"{header_segment}.{payload_segment}".encode("ascii"),
            )
            selected_now = (
                datetime.now(timezone.utc)
                if now is None
                else _utc(now, name="verify time")
            )
            if (
                claims.get("iss") != self.issuer
                or claims.get("aud") != audience
                or type(claims.get("iat")) is not int
                or type(claims.get("exp")) is not int
                or claims["iat"] > int(selected_now.timestamp())
                or claims["exp"] <= int(selected_now.timestamp())
            ):
                raise ValueError
        except (
            InvalidSignature,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            raise ValueError("Task token invalid") from None
        return claims
