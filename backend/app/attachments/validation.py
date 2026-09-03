from __future__ import annotations

import hashlib
import tempfile
import warnings
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from .conversation_models import MAX_FILE_BYTES

_CHUNK_BYTES = 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_OFFICE_ENTRIES = 2_048
_MAX_OFFICE_ENTRY_BYTES = 10 * 1024 * 1024
_MAX_OFFICE_TOTAL_BYTES = 100 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_OFFICE_TYPES = (
    (
        "word/document.xml",
        b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    (
        "xl/workbook.xml",
        b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    (
        "ppt/presentation.xml",
        b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
)
_FORBIDDEN_OFFICE_NAMES = (
    "vbaproject",
    "macrosheets/",
    "externallinks/",
    "embeddings/",
    "activex/",
)
_ACTIVE_TEXT_PREFIXES = (
    b"#!",
    b"<!doctype html",
    b"<html",
    b"<svg",
    b"<script",
    b"<?php",
)
_ACTIVE_PDF_TOKENS = (
    b"/encrypt",
    b"/javascript",
    b"/openaction",
    b"/launch",
    b"/richmedia",
    b"/embeddedfile",
    b"/xfa",
)


class AttachmentValidationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("attachment validation rejected")


@dataclass(repr=False)
class OpenedObject:
    stream: BinaryIO = field(repr=False)
    size: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 0
            or not hasattr(self.stream, "read")
        ):
            raise ValueError("opened attachment invalid")

    def __repr__(self) -> str:
        return f"OpenedObject(stream=<redacted>, size={self.size!r})"

    def iter_chunks(self, chunk_bytes: int = _CHUNK_BYTES) -> Iterator[bytes]:
        if (
            isinstance(chunk_bytes, bool)
            or not isinstance(chunk_bytes, int)
            or chunk_bytes <= 0
            or chunk_bytes > _CHUNK_BYTES
        ):
            raise ValueError("attachment chunk size invalid")
        while True:
            chunk = self.stream.read(chunk_bytes)
            if not isinstance(chunk, bytes):
                raise AttachmentValidationError("invalid_stream")
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        self.stream.close()


@dataclass(frozen=True)
class ValidationResult:
    detected_mime: str
    size_bytes: int
    sha256: bytes = field(repr=False)
    coverage: Mapping[str, object]


class AttachmentValidator:
    def __init__(self, *, max_file_bytes: int = MAX_FILE_BYTES) -> None:
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or max_file_bytes <= 0
            or max_file_bytes > MAX_FILE_BYTES
        ):
            raise ValueError("attachment validation limit invalid")
        self._max_file_bytes = max_file_bytes

    def validate(
        self,
        source: OpenedObject,
        *,
        expected_size: int,
        expected_sha256: bytes,
    ) -> ValidationResult:
        if (
            not isinstance(source, OpenedObject)
            or isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < 0
            or expected_size > self._max_file_bytes
            or not isinstance(expected_sha256, bytes)
            or len(expected_sha256) != 32
        ):
            raise AttachmentValidationError("integrity_mismatch")

        digest = hashlib.sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=_CHUNK_BYTES) as staged:
            for chunk in source.iter_chunks():
                size += len(chunk)
                if size > self._max_file_bytes:
                    raise AttachmentValidationError("file_too_large")
                digest.update(chunk)
                staged.write(chunk)
            checksum = digest.digest()
            if (
                size != source.size
                or size != expected_size
                or checksum != expected_sha256
            ):
                raise AttachmentValidationError("integrity_mismatch")
            staged.seek(0)
            detected_mime, coverage = self._inspect(staged, size)
        return ValidationResult(detected_mime, size, checksum, coverage)

    def _inspect(self, staged: BinaryIO, size: int) -> tuple[str, dict[str, object]]:
        header = staged.read(min(size, 16))
        staged.seek(0)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            self._verify_image(staged, "PNG")
            return "image/png", self._coverage("safe_thumbnail")
        if header.startswith(b"\xff\xd8\xff"):
            self._verify_image(staged, "JPEG")
            return "image/jpeg", self._coverage("safe_thumbnail")
        if header.startswith(b"%PDF-"):
            self._verify_pdf(staged, size)
            return "application/pdf", self._coverage("first_page")
        if header.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            return self._verify_office(staged), self._coverage("metadata_only")
        return self._verify_text(staged), self._coverage("metadata_only")

    @staticmethod
    def _coverage(kind: str) -> dict[str, object]:
        return {
            "coverage": kind,
            "download": True,
            "inline_preview": kind != "metadata_only",
        }

    @staticmethod
    def _verify_image(staged: BinaryIO, expected_format: str) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(staged) as image:
                    if (
                        image.format != expected_format
                        or image.width <= 0
                        or image.height <= 0
                        or image.width * image.height > _MAX_IMAGE_PIXELS
                    ):
                        raise AttachmentValidationError("invalid_image")
                    image.verify()
        except AttachmentValidationError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            ValueError,
        ):
            raise AttachmentValidationError("invalid_image") from None

    @staticmethod
    def _verify_pdf(staged: BinaryIO, size: int) -> None:
        data = staged.read(size + 1)
        lowered = data.lower()
        if len(data) != size or b"%%eof" not in lowered[-1024:]:
            raise AttachmentValidationError("truncated_document")
        if b"/encrypt" in lowered:
            raise AttachmentValidationError("encrypted_document")
        if any(token in lowered for token in _ACTIVE_PDF_TOKENS[1:]):
            raise AttachmentValidationError("active_content")

    @staticmethod
    def _verify_office(staged: BinaryIO) -> str:
        try:
            with zipfile.ZipFile(staged) as archive:
                entries = archive.infolist()
                if not entries or len(entries) > _MAX_OFFICE_ENTRIES:
                    raise AttachmentValidationError("archive_limits_exceeded")
                total = 0
                seen: set[str] = set()
                for entry in entries:
                    normalized = entry.filename.replace("\\", "/")
                    folded = normalized.casefold()
                    total += entry.file_size
                    ratio = entry.file_size / max(1, entry.compress_size)
                    if (
                        entry.flag_bits & 1
                        or folded in seen
                        or normalized.startswith("/")
                        or ".." in normalized.split("/")
                        or entry.file_size > _MAX_OFFICE_ENTRY_BYTES
                        or total > _MAX_OFFICE_TOTAL_BYTES
                        or ratio > _MAX_COMPRESSION_RATIO
                    ):
                        reason = (
                            "encrypted_document"
                            if entry.flag_bits & 1
                            else "archive_limits_exceeded"
                        )
                        raise AttachmentValidationError(reason)
                    if any(value in folded for value in _FORBIDDEN_OFFICE_NAMES):
                        raise AttachmentValidationError("active_content")
                    seen.add(folded)
                if "[content_types].xml" not in seen:
                    raise AttachmentValidationError("invalid_office")
                content_name = next(
                    entry.filename
                    for entry in entries
                    if entry.filename.casefold() == "[content_types].xml"
                )
                content_types = archive.read(content_name)
                lowered_types = content_types.lower()
                if (
                    len(content_types) > _MAX_OFFICE_ENTRY_BYTES
                    or b"<!doctype" in lowered_types
                    or b"<!entity" in lowered_types
                ):
                    raise AttachmentValidationError("active_content")
                selected_mime = None
                for root_name, marker, mime in _OFFICE_TYPES:
                    if root_name in seen and marker in content_types:
                        selected_mime = mime
                        break
                if selected_mime is None:
                    raise AttachmentValidationError("invalid_office")
                for entry in entries:
                    if not entry.is_dir():
                        with archive.open(entry) as member:
                            while member.read(_CHUNK_BYTES):
                                pass
                return selected_mime
        except AttachmentValidationError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
            raise AttachmentValidationError("invalid_office") from None

    @staticmethod
    def _verify_text(staged: BinaryIO) -> str:
        data = staged.read(MAX_FILE_BYTES + 1)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise AttachmentValidationError("unsupported_type") from None
        lowered = data.lstrip().lower()
        if any(lowered.startswith(prefix) for prefix in _ACTIVE_TEXT_PREFIXES):
            raise AttachmentValidationError("active_content")
        if "\0" in text or any(
            ord(character) < 32 and character not in "\t\n\r\f" for character in text
        ):
            raise AttachmentValidationError("unsupported_type")
        return "text/plain"
