from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from .models import canonical_panorama_url

PRODUCTION_EVIDENCE_ROOT = Path("/data/agent-platform/hr-intelligence/evidence")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-language",
        "content-type",
        "date",
        "etag",
        "last-modified",
    }
)


class EvidenceError(RuntimeError):
    pass


class EvidenceCorrupt(EvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class EvidencePayload:
    source_url: str
    mime: str
    body: bytes
    request_headers: Mapping[str, str] | None = None
    response_headers: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_url", canonical_panorama_url(self.source_url))
        if not isinstance(self.mime, str) or not 1 <= len(self.mime.strip()) <= 255:
            raise ValueError("evidence mime invalid")
        object.__setattr__(self, "mime", self.mime.strip())
        if not isinstance(self.body, bytes) or len(self.body) > 10 * 1024 * 1024:
            raise ValueError("evidence body invalid")
        for headers in (self.request_headers, self.response_headers):
            if headers is not None and (
                not isinstance(headers, Mapping)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in headers.items()
                )
            ):
                raise ValueError("evidence headers invalid")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    sha256: str
    locator: str
    mime: str
    size_bytes: int
    metadata_json: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sha256, str)
            or _SHA256.fullmatch(self.sha256) is None
            or self.locator != f"sha256/{self.sha256[:2]}/{self.sha256}"
            or not isinstance(self.mime, str)
            or not 1 <= len(self.mime) <= 255
            or isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or not 0 <= self.size_bytes <= 10 * 1024 * 1024
        ):
            raise ValueError("evidence record invalid")
        try:
            metadata = json.loads(self.metadata_json)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("evidence metadata invalid") from None
        if not isinstance(metadata, dict):
            raise TypeError("evidence metadata invalid")


class EvidenceArchive:
    def __init__(
        self,
        root: str | Path = PRODUCTION_EVIDENCE_ROOT,
        *,
        production: bool = False,
    ) -> None:
        selected = Path(root).expanduser().resolve()
        if production and selected != PRODUCTION_EVIDENCE_ROOT:
            raise ValueError("production evidence must be stored under /data")
        self._root = selected

    @property
    def root(self) -> Path:
        return self._root

    def store(self, payload: EvidencePayload) -> EvidenceRecord:
        if not isinstance(payload, EvidencePayload):
            raise TypeError("evidence payload required")
        sha256 = hashlib.sha256(payload.body).hexdigest()
        locator = f"sha256/{sha256[:2]}/{sha256}"
        response_headers = {
            key.lower(): value
            for key, value in (payload.response_headers or {}).items()
            if key.lower() in _SAFE_RESPONSE_HEADERS
        }
        metadata = {
            "mime": payload.mime,
            "response_headers": dict(sorted(response_headers.items())),
            "sha256": sha256,
            "size_bytes": len(payload.body),
            "source_url": payload.source_url,
        }
        metadata_json = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        record = EvidenceRecord(
            sha256=sha256,
            locator=locator,
            mime=payload.mime,
            size_bytes=len(payload.body),
            metadata_json=metadata_json,
        )
        object_path = self._root / locator
        metadata_sha256 = hashlib.sha256(metadata_json.encode()).hexdigest()
        metadata_path = object_path.with_name(
            f"{object_path.name}.metadata.{metadata_sha256}.json"
        )
        if object_path.exists():
            if self._verified_bytes(object_path, sha256) != payload.body:
                raise EvidenceCorrupt("evidence object hash mismatch")
            if (
                metadata_path.exists()
                and metadata_path.read_text("utf-8") != metadata_json
            ):
                raise EvidenceCorrupt("evidence metadata mismatch")
            if not metadata_path.exists():
                self._atomic_create(metadata_path, metadata_json.encode())
            return record

        object_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_create(object_path, payload.body)
        try:
            self._atomic_create(metadata_path, metadata_json.encode())
        except Exception:
            object_path.unlink(missing_ok=True)
            raise
        return record

    def read(self, sha256: str) -> bytes:
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise ValueError("evidence hash invalid")
        path = self._root / f"sha256/{sha256[:2]}/{sha256}"
        if not path.is_file():
            raise EvidenceError("evidence object unavailable")
        return self._verified_bytes(path, sha256)

    @staticmethod
    def _verified_bytes(path: Path, sha256: str) -> bytes:
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha256:
            raise EvidenceCorrupt("evidence object hash mismatch")
        return body

    @staticmethod
    def _atomic_create(path: Path, body: bytes) -> None:
        staging = path.parent / f".{path.name}.{uuid4().hex}.part"
        try:
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(staging, path)
            except FileExistsError:
                pass
        finally:
            staging.unlink(missing_ok=True)


def sanitized_response_headers(headers: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(
        {
            key.lower(): value
            for key, value in headers.items()
            if key.lower() in _SAFE_RESPONSE_HEADERS
        }
    )


EvidenceStore = EvidenceArchive


__all__ = [
    "PRODUCTION_EVIDENCE_ROOT",
    "EvidenceArchive",
    "EvidenceCorrupt",
    "EvidenceError",
    "EvidencePayload",
    "EvidenceRecord",
    "EvidenceStore",
    "sanitized_response_headers",
]
