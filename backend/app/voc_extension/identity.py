"""Short-lived, actor-bound identity tokens for the VOC workspace."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import time
from pathlib import Path
from uuid import UUID, uuid4

VOC_CAPABILITIES = frozenset({"voc.submit", "voc.read_self", "voc.read_all"})


def _segment(value: dict[str, object]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def read_signing_key(path_value: str | Path) -> bytes:
    """Read a service-owned regular 0600 signing key without following symlinks."""

    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeError("VOC extension signing key must use an absolute path")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError(
            "VOC extension signing key must be a regular mode 0600 file"
        ) from error
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(
            "VOC extension signing key must be a regular mode 0600 file"
        )
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError("VOC extension signing key must use mode 0600")
    if metadata.st_uid != os.getuid():
        raise RuntimeError("VOC extension signing key must be owned by the service user")
    try:
        secret = path.read_bytes()
    except OSError as error:
        raise RuntimeError("VOC extension signing key is unavailable") from error
    if len(secret) < 32:
        raise RuntimeError("VOC extension signing key must contain at least 32 bytes")
    return secret


class PlatformVocTokenSigner:
    """Issue the exact narrow token understood by the VOC service."""

    def __init__(self, secret: bytes) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("VOC signing secret must contain at least 32 bytes")
        self._secret = secret

    @classmethod
    def from_file(cls, path: str | Path) -> PlatformVocTokenSigner:
        return cls(read_signing_key(path))

    def issue(
        self,
        actor_id: UUID,
        capabilities: set[str] | frozenset[str],
        *,
        now: int | None = None,
        jti: UUID | None = None,
    ) -> str:
        selected_capabilities = frozenset(capabilities)
        if not selected_capabilities or not selected_capabilities.issubset(
            VOC_CAPABILITIES
        ):
            raise ValueError("VOC capabilities are invalid")
        if not isinstance(actor_id, UUID):
            raise ValueError("VOC actor ID must be a UUID")
        issued_at = int(time.time()) if now is None else now
        if type(issued_at) is not int:
            raise ValueError("VOC issue time must be an integer")
        token_id = uuid4() if jti is None else jti
        if not isinstance(token_id, UUID):
            raise ValueError("VOC token ID must be a UUID")

        header = _segment({"alg": "HS256", "typ": "JWT"})
        payload = _segment(
            {
                "iss": "orbbec-agent-platform",
                "aud": "orbbec-voc",
                "sub": str(actor_id),
                "capabilities": sorted(selected_capabilities),
                "iat": issued_at,
                "exp": issued_at + 60,
                "jti": str(token_id),
            }
        )
        signing_input = f"{header}.{payload}"
        signature = base64.urlsafe_b64encode(
            hmac.new(
                self._secret,
                signing_input.encode("ascii"),
                hashlib.sha256,
            ).digest()
        ).rstrip(b"=")
        return f"{signing_input}.{signature.decode('ascii')}"
