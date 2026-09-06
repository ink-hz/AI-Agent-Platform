from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol
from uuid import UUID

from .panorama_repository import PanoramaNotFound, PanoramaUnavailable

DocumentName = Literal["report.md", "report.pdf", "report.xlsx"]

_DOCUMENTS = frozenset({"report.md", "report.pdf", "report.xlsx"})
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MIME = re.compile(r"[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}\Z")
_MAX_FILE_BYTES = 64 * 1024 * 1024


class BundleDocumentRepository(Protocol):
    def bundle(self, bundle_id: UUID) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class VerifiedDocument:
    name: str
    mime: str
    sha256: str
    body: bytes


class IntelligenceDocumentStore:
    """Read immutable Bundle documents and evidence after verifying their index."""

    def __init__(self, root: str | Path, repository: BundleDocumentRepository) -> None:
        selected = Path(root)
        if not selected.is_absolute() or not callable(getattr(repository, "bundle", None)):
            raise ValueError("intelligence document store invalid")
        self._root = selected.resolve()
        self._repository = repository

    def read_document(self, bundle_id: UUID, name: DocumentName) -> VerifiedDocument:
        if not isinstance(bundle_id, UUID) or name not in _DOCUMENTS:
            raise TypeError("intelligence document identifier invalid")
        bundle = self._repository.bundle(bundle_id)
        index = bundle.get("document_index")
        record = index.get(name) if isinstance(index, Mapping) else None
        if not isinstance(record, Mapping):
            raise PanoramaNotFound("intelligence document not found")
        return self._read(bundle, name, record)

    def read_evidence(self, bundle_id: UUID, sha256: str) -> VerifiedDocument:
        if not isinstance(bundle_id, UUID) or not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise TypeError("intelligence evidence identifier invalid")
        bundle = self._repository.bundle(bundle_id)
        index = bundle.get("evidence_index")
        if not isinstance(index, (list, tuple)):
            raise PanoramaUnavailable("intelligence evidence index invalid")
        record = next(
            (item for item in index if isinstance(item, Mapping) and item.get("sha256") == sha256),
            None,
        )
        if record is None:
            raise PanoramaNotFound("intelligence evidence not found")
        expected_locator = f"evidence/sha256/{sha256[:2]}/{sha256}"
        if record.get("locator") != expected_locator:
            raise PanoramaUnavailable("intelligence evidence locator invalid")
        return self._read(bundle, expected_locator, record, expected_sha256=sha256)

    def read_indexed_path(
        self,
        bundle_id: UUID,
        relative_path: str,
        *,
        expected_mime: str,
    ) -> VerifiedDocument:
        if not isinstance(bundle_id, UUID) or not isinstance(relative_path, str):
            raise TypeError("intelligence document identifier invalid")
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or relative.parts[0] != "agent"
        ):
            raise PanoramaUnavailable("intelligence document locator invalid")
        if not isinstance(expected_mime, str) or _MIME.fullmatch(expected_mime) is None:
            raise TypeError("intelligence document MIME invalid")
        bundle = self._repository.bundle(bundle_id)
        index = bundle.get("agent_document_index")
        record = index.get(relative_path) if isinstance(index, Mapping) else None
        if not isinstance(record, Mapping):
            raise PanoramaNotFound("intelligence document not found")
        selected = self._read(bundle, relative_path, record)
        if selected.mime != expected_mime:
            raise PanoramaUnavailable("intelligence document MIME mismatch")
        return selected

    def _read(
        self,
        bundle: Mapping[str, object],
        relative_name: str,
        record: Mapping[str, object],
        *,
        expected_sha256: str | None = None,
    ) -> VerifiedDocument:
        bundle_id = bundle.get("bundle_id")
        locator = bundle.get("bundle_locator")
        if not isinstance(bundle_id, UUID) or locator != f"bundles/{bundle_id}":
            raise PanoramaUnavailable("intelligence bundle locator invalid")
        relative = PurePosixPath(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise PanoramaUnavailable("intelligence document locator invalid")
        bundle_path = self._root / "bundles" / str(bundle_id)
        path = bundle_path.joinpath(*relative.parts)
        if bundle_path.is_symlink() or path.is_symlink() or not path.is_file():
            raise PanoramaUnavailable("intelligence document unavailable")
        try:
            if not path.resolve().is_relative_to(bundle_path.resolve()):
                raise PanoramaUnavailable("intelligence document locator invalid")
            size = path.stat().st_size
            indexed_size = record.get("size_bytes")
            raw_mime = record.get("mime")
            sha256 = record.get("sha256")
            if (
                isinstance(indexed_size, bool)
                or not isinstance(indexed_size, int)
                or indexed_size < 0
                or indexed_size > _MAX_FILE_BYTES
                or size != indexed_size
                or not isinstance(raw_mime, str)
                or not isinstance(sha256, str)
                or _SHA256.fullmatch(sha256) is None
                or (expected_sha256 is not None and sha256 != expected_sha256)
            ):
                raise PanoramaUnavailable("intelligence document index invalid")
            mime = raw_mime.split(";", 1)[0].strip().lower()
            if _MIME.fullmatch(mime) is None:
                raise PanoramaUnavailable("intelligence document MIME invalid")
            body = path.read_bytes()
        except OSError:
            raise PanoramaUnavailable("intelligence document unavailable") from None
        if hashlib.sha256(body).hexdigest() != sha256:
            raise PanoramaUnavailable("intelligence document checksum mismatch")
        return VerifiedDocument(relative.as_posix(), mime, sha256, body)


__all__ = ["DocumentName", "IntelligenceDocumentStore", "VerifiedDocument"]
