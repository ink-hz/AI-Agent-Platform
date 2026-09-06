from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from .intelligence_documents import IntelligenceDocumentStore
from .panorama_repository import PanoramaUnavailable

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_CHUNK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_MAX_CHUNK_BYTES = 24 * 1024


@dataclass(frozen=True, slots=True)
class VerifiedMarkdownChunk:
    chunk_id: str
    path: str
    sha256: str
    text: str


class IntelligenceMarkdownStore:
    """Read exact immutable Markdown ranges without interpreting their content."""

    def __init__(self, documents: IntelligenceDocumentStore) -> None:
        if not isinstance(documents, IntelligenceDocumentStore):
            raise TypeError("intelligence document store required")
        self._documents = documents

    def read_chunk(
        self,
        bundle_id: UUID,
        record: Mapping[str, object],
    ) -> VerifiedMarkdownChunk:
        if not isinstance(bundle_id, UUID) or not isinstance(record, Mapping):
            raise TypeError("intelligence Markdown chunk invalid")
        chunk_id = record.get("chunk_id")
        path = record.get("path")
        sha256 = record.get("sha256")
        byte_start = record.get("byte_start")
        byte_end = record.get("byte_end")
        if (
            not isinstance(chunk_id, str)
            or _CHUNK_ID.fullmatch(chunk_id) is None
            or not isinstance(path, str)
            or not path.endswith(".md")
            or not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
            or isinstance(byte_start, bool)
            or not isinstance(byte_start, int)
            or isinstance(byte_end, bool)
            or not isinstance(byte_end, int)
            or byte_start < 0
            or byte_end <= byte_start
            or byte_end - byte_start > _MAX_CHUNK_BYTES
        ):
            raise PanoramaUnavailable("intelligence Markdown chunk invalid")
        document = self._documents.read_indexed_path(
            bundle_id,
            path,
            expected_mime="text/markdown",
        )
        if byte_end > len(document.body):
            raise PanoramaUnavailable("intelligence Markdown chunk bounds invalid")
        body = document.body[byte_start:byte_end]
        if hashlib.sha256(body).hexdigest() != sha256:
            raise PanoramaUnavailable("intelligence Markdown chunk checksum mismatch")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise PanoramaUnavailable("intelligence Markdown chunk encoding invalid") from None
        return VerifiedMarkdownChunk(chunk_id, path, sha256, text)


__all__ = ["IntelligenceMarkdownStore", "VerifiedMarkdownChunk"]
