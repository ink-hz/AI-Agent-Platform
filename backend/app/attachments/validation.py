from __future__ import annotations

import hashlib
import posixpath
import re
import struct
import tempfile
import urllib.parse
import warnings
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import BinaryIO

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

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
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
    ),
    (
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}workbook",
    ),
    (
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "{http://schemas.openxmlformats.org/presentationml/2006/main}presentation",
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
_PDF_FORBIDDEN_NAMES = {
    "/aa",
    "/embeddedfile",
    "/embeddedfiles",
    "/filespec",
    "/gotoe",
    "/gotor",
    "/importdata",
    "/javascript",
    "/js",
    "/launch",
    "/movie",
    "/openaction",
    "/rendition",
    "/richmedia",
    "/screen",
    "/sound",
    "/submitform",
    "/uri",
    "/xfa",
    "/3d",
    "/3dd",
    "/3dv",
}
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_DOCUMENT_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "officeDocument"
)
_FORBIDDEN_RELATIONSHIP_TYPES = (
    "activex",
    "attachedtemplate",
    "connections",
    "customui",
    "externaldata",
    "externallink",
    "hyperlink",
    "oleobject",
    "package",
    "querytable",
    "vbaproject",
)
_FORBIDDEN_CONTENT_TYPES = (
    "activex",
    "connections",
    "customui",
    "externaldata",
    "macroenabled",
    "oleobject",
    "querytable",
    "vba",
)
_ALLOWED_CONTENT_TYPES = {
    "application/xml",
    "text/xml",
    "application/vnd.openxmlformats-package.relationships+xml",
    "application/vnd.openxmlformats-package.core-properties+xml",
    "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "application/vnd.openxmlformats-officedocument.custom-properties+xml",
    "application/vnd.openxmlformats-officedocument.theme+xml",
    "application/vnd.openxmlformats-officedocument.themeoverride+xml",
    "application/vnd.openxmlformats-officedocument.drawing+xml",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/tiff",
    "image/bmp",
}
_ALLOWED_CONTENT_TYPE_PREFIXES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.",
    "application/vnd.openxmlformats-officedocument.presentationml.",
    "application/vnd.openxmlformats-officedocument.drawingml.",
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
            self._verify_image(staged, "PNG", size)
            return "image/png", self._coverage("safe_thumbnail")
        if header.startswith(b"\xff\xd8\xff"):
            self._verify_image(staged, "JPEG", size)
            return "image/jpeg", self._coverage("safe_thumbnail")
        if header.startswith(b"%PDF-"):
            self._verify_pdf(staged, size)
            return "application/pdf", self._coverage("first_page")
        if header.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            return self._verify_office(staged), self._coverage("metadata_only")
        if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise AttachmentValidationError("encrypted_document")
        return self._verify_text(staged), self._coverage("metadata_only")

    @staticmethod
    def _coverage(kind: str) -> dict[str, object]:
        return {
            "coverage": kind,
            "download": True,
            "inline_preview": kind != "metadata_only",
        }

    @staticmethod
    def _verify_image(staged: BinaryIO, expected_format: str, size: int) -> None:
        try:
            data = staged.read(size + 1)
            if len(data) != size:
                raise AttachmentValidationError("invalid_image")
            if expected_format == "PNG" and not AttachmentValidator._png_complete(data):
                raise AttachmentValidationError("invalid_image")
            if expected_format == "JPEG" and not AttachmentValidator._jpeg_complete(data):
                raise AttachmentValidationError("invalid_image")
            staged.seek(0)
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
    def _png_complete(data: bytes) -> bool:
        offset = 8
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset : offset + 4], "big")
            end = offset + 12 + length
            if end > len(data):
                return False
            kind = data[offset + 4 : offset + 8]
            if kind == b"IEND":
                return length == 0 and end == len(data)
            offset = end
        return False

    @staticmethod
    def _jpeg_complete(data: bytes) -> bool:
        if not data.startswith(b"\xff\xd8"):
            return False
        offset = 2
        in_scan = False
        while offset < len(data):
            if in_scan:
                marker = data.find(b"\xff", offset)
                if marker < 0 or marker + 1 >= len(data):
                    return False
                next_byte = marker + 1
                while next_byte < len(data) and data[next_byte] == 0xFF:
                    next_byte += 1
                if next_byte >= len(data):
                    return False
                code = data[next_byte]
                if code == 0x00 or 0xD0 <= code <= 0xD7:
                    offset = next_byte + 1
                    continue
                if code == 0xD9:
                    return next_byte + 1 == len(data)
                offset = marker
                in_scan = False
                continue
            if data[offset] != 0xFF:
                return False
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                return False
            code = data[offset]
            offset += 1
            if code == 0xD9:
                return offset == len(data)
            if code in {0x01, *range(0xD0, 0xD8)} or code == 0xD8:
                if code == 0xD8:
                    return False
                continue
            if offset + 2 > len(data):
                return False
            length = int.from_bytes(data[offset : offset + 2], "big")
            if length < 2 or offset + length > len(data):
                return False
            offset += length
            if code == 0xDA:
                in_scan = True
        return False

    @staticmethod
    def _verify_pdf(staged: BinaryIO, size: int) -> None:
        data = staged.read(size + 1)
        if len(data) != size or not re.search(rb"%%EOF[\x00\x09\x0a\x0c\x0d\x20]*\Z", data):
            raise AttachmentValidationError("truncated_document")
        staged.seek(0)
        try:
            reader = PdfReader(staged, strict=True)
            if reader.is_encrypted or "/Encrypt" in reader.trailer:
                raise AttachmentValidationError("encrypted_document")
            if not reader.pages:
                raise AttachmentValidationError("invalid_document")
            AttachmentValidator._walk_pdf(reader.trailer)
        except AttachmentValidationError:
            raise
        except (PdfReadError, OSError, RuntimeError, TypeError, ValueError):
            raise AttachmentValidationError("invalid_document") from None

    @staticmethod
    def _walk_pdf(root: object) -> None:
        pending: list[tuple[object, int]] = [(root, 0)]
        seen: set[tuple[int, int] | int] = set()
        visited = 0
        while pending:
            value, depth = pending.pop()
            if depth > 64:
                raise AttachmentValidationError("invalid_document")
            if isinstance(value, IndirectObject):
                identity = (value.idnum, value.generation)
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    value = value.get_object()
                except Exception as error:
                    raise AttachmentValidationError("invalid_document") from error
            else:
                identity = id(value)
                if identity in seen:
                    continue
                seen.add(identity)
            visited += 1
            if visited > 20_000:
                raise AttachmentValidationError("invalid_document")
            if isinstance(value, DictionaryObject):
                for key, child in value.items():
                    normalized = str(key).casefold()
                    if normalized in _PDF_FORBIDDEN_NAMES:
                        raise AttachmentValidationError("active_content")
                    if isinstance(child, NameObject) and str(child).casefold() in _PDF_FORBIDDEN_NAMES:
                        raise AttachmentValidationError("active_content")
                    pending.append((child, depth + 1))
            elif isinstance(value, ArrayObject):
                pending.extend((child, depth + 1) for child in value)

    @staticmethod
    def _verify_office(staged: BinaryIO) -> str:
        try:
            if not AttachmentValidator._zip_complete(staged):
                raise AttachmentValidationError("invalid_office")
            staged.seek(0)
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
                        or "\\" in entry.filename
                        or normalized != posixpath.normpath(normalized)
                        or any(not part or part == "." for part in normalized.split("/"))
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
                AttachmentValidator._verify_zip_local_headers(staged, entries)
                if "[content_types].xml" not in seen:
                    raise AttachmentValidationError("invalid_office")
                content_name = next(
                    entry.filename
                    for entry in entries
                    if entry.filename.casefold() == "[content_types].xml"
                )
                content_types = AttachmentValidator._safe_xml(
                    archive.read(content_name)
                )
                if content_types.tag != f"{{{_CONTENT_TYPES_NS}}}Types":
                    raise AttachmentValidationError("invalid_office")
                overrides: dict[str, str] = {}
                defaults: dict[str, str] = {}
                for child in content_types:
                    if child.tag not in {
                        f"{{{_CONTENT_TYPES_NS}}}Default",
                        f"{{{_CONTENT_TYPES_NS}}}Override",
                    }:
                        raise AttachmentValidationError("invalid_office")
                    content_type = child.attrib.get("ContentType", "")
                    if not content_type or any(
                        forbidden in content_type.casefold()
                        for forbidden in _FORBIDDEN_CONTENT_TYPES
                    ):
                        raise AttachmentValidationError("active_content")
                    folded_type = content_type.casefold()
                    if not (
                        folded_type in _ALLOWED_CONTENT_TYPES
                        or any(
                            folded_type.startswith(prefix)
                            and folded_type.endswith("+xml")
                            for prefix in _ALLOWED_CONTENT_TYPE_PREFIXES
                        )
                    ):
                        raise AttachmentValidationError("invalid_office")
                    if child.tag.endswith("Override"):
                        part = child.attrib.get("PartName", "")
                        if not part.startswith("/"):
                            raise AttachmentValidationError("invalid_office")
                        part = part[1:]
                        if part.casefold() in overrides:
                            raise AttachmentValidationError("invalid_office")
                        overrides[part.casefold()] = content_type
                    else:
                        extension = child.attrib.get("Extension", "").casefold()
                        if (
                            not extension
                            or "." in extension
                            or "/" in extension
                            or extension in defaults
                        ):
                            raise AttachmentValidationError("invalid_office")
                        defaults[extension] = content_type

                for entry in entries:
                    folded_name = entry.filename.casefold()
                    if entry.is_dir() or folded_name == "[content_types].xml":
                        continue
                    extension = folded_name.rsplit(".", 1)[-1] if "." in folded_name else ""
                    if folded_name not in overrides and extension not in defaults:
                        raise AttachmentValidationError("invalid_office")

                if "_rels/.rels" not in seen:
                    raise AttachmentValidationError("invalid_office")
                package_rels = AttachmentValidator._relationships(
                    archive.read(next(e.filename for e in entries if e.filename.casefold() == "_rels/.rels")),
                    base="",
                    names=seen,
                )
                roots = [target for rel_type, target in package_rels if rel_type == _OFFICE_DOCUMENT_REL]
                if len(roots) != 1:
                    raise AttachmentValidationError("invalid_office")
                selected_mime = None
                for root_name, marker, mime, root_tag in _OFFICE_TYPES:
                    if roots[0].casefold() == root_name and overrides.get(root_name) == marker:
                        root = AttachmentValidator._safe_xml(
                            archive.read(next(e.filename for e in entries if e.filename.casefold() == root_name))
                        )
                        if root.tag != root_tag:
                            raise AttachmentValidationError("invalid_office")
                        selected_mime = mime
                        break
                if selected_mime is None:
                    raise AttachmentValidationError("invalid_office")
                for entry in entries:
                    if entry.filename.casefold().endswith(".rels") and entry.filename.casefold() != "_rels/.rels":
                        parent = entry.filename.replace("\\", "/")
                        rel_dir = posixpath.dirname(posixpath.dirname(parent))
                        AttachmentValidator._relationships(
                            archive.read(entry), base=rel_dir, names=seen
                        )
                for entry in entries:
                    if not entry.is_dir():
                        with archive.open(entry) as member:
                            while member.read(_CHUNK_BYTES):
                                pass
                return selected_mime
        except AttachmentValidationError:
            raise
        except (OSError, RuntimeError, KeyError, zipfile.BadZipFile, zipfile.LargeZipFile):
            raise AttachmentValidationError("invalid_office") from None

    @staticmethod
    def _verify_zip_local_headers(staged: BinaryIO, entries: list[zipfile.ZipInfo]) -> None:
        for entry in entries:
            staged.seek(entry.header_offset)
            header = staged.read(30)
            if len(header) != 30 or header[:4] != b"PK\x03\x04":
                raise AttachmentValidationError("invalid_office")
            (
                flags,
                method,
                crc,
                compressed_size,
                file_size,
                name_length,
                extra_length,
            ) = (
                struct.unpack_from("<H", header, 6)[0],
                struct.unpack_from("<H", header, 8)[0],
                struct.unpack_from("<I", header, 14)[0],
                struct.unpack_from("<I", header, 18)[0],
                struct.unpack_from("<I", header, 22)[0],
                struct.unpack_from("<H", header, 26)[0],
                struct.unpack_from("<H", header, 28)[0],
            )
            local_name = staged.read(name_length)
            try:
                decoded_name = local_name.decode(
                    "utf-8" if flags & 0x800 else "cp437"
                )
            except UnicodeDecodeError:
                raise AttachmentValidationError("invalid_office") from None
            if flags != entry.flag_bits:
                reason = "encrypted_document" if (flags | entry.flag_bits) & 1 else "invalid_office"
                raise AttachmentValidationError(reason)
            if flags & 1:
                raise AttachmentValidationError("encrypted_document")
            if (
                method != entry.compress_type
                or method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or decoded_name != entry.filename
            ):
                raise AttachmentValidationError("invalid_office")
            if not flags & 0x08 and (
                crc != entry.CRC
                or compressed_size != entry.compress_size
                or file_size != entry.file_size
            ):
                raise AttachmentValidationError("invalid_office")
            if entry.header_offset + 30 + name_length + extra_length + entry.compress_size > staged.seek(0, 2):
                raise AttachmentValidationError("invalid_office")

    @staticmethod
    def _zip_complete(staged: BinaryIO) -> bool:
        staged.seek(0, 2)
        size = staged.tell()
        if size < 22:
            return False
        staged.seek(max(0, size - 65_557))
        tail = staged.read()
        marker = tail.rfind(b"PK\x05\x06")
        if marker < 0 or marker + 22 > len(tail):
            return False
        comment_length = int.from_bytes(tail[marker + 20 : marker + 22], "little")
        return marker + 22 + comment_length == len(tail)

    @staticmethod
    def _safe_xml(data: bytes):
        if len(data) > _MAX_OFFICE_ENTRY_BYTES:
            raise AttachmentValidationError("archive_limits_exceeded")
        try:
            return SafeElementTree.fromstring(data)
        except (DefusedXmlException, SafeElementTree.ParseError, ValueError):
            raise AttachmentValidationError("invalid_office") from None

    @staticmethod
    def _relationships(data: bytes, *, base: str, names: set[str]) -> list[tuple[str, str]]:
        root = AttachmentValidator._safe_xml(data)
        if root.tag != f"{{{_RELATIONSHIPS_NS}}}Relationships":
            raise AttachmentValidationError("invalid_office")
        relationships: list[tuple[str, str]] = []
        identifiers: set[str] = set()
        for child in root:
            if child.tag != f"{{{_RELATIONSHIPS_NS}}}Relationship":
                raise AttachmentValidationError("invalid_office")
            identifier = child.attrib.get("Id", "")
            rel_type = child.attrib.get("Type", "")
            target = child.attrib.get("Target", "")
            mode = child.attrib.get("TargetMode", "Internal")
            if (
                not identifier
                or identifier in identifiers
                or not rel_type
                or not target
                or mode != "Internal"
                or any(token in rel_type.casefold() for token in _FORBIDDEN_RELATIONSHIP_TYPES)
            ):
                reason = "active_content" if mode != "Internal" or rel_type else "invalid_office"
                raise AttachmentValidationError(reason)
            identifiers.add(identifier)
            split = urllib.parse.urlsplit(target)
            decoded = urllib.parse.unquote(target).replace("\\", "/")
            if (
                split.scheme
                or split.netloc
                or split.query
                or split.fragment
                or decoded.startswith("/")
                or "\\" in target
            ):
                raise AttachmentValidationError("archive_limits_exceeded")
            resolved = posixpath.normpath(posixpath.join(base, decoded))
            if resolved.startswith("../") or resolved == ".." or resolved.casefold() not in names:
                raise AttachmentValidationError("archive_limits_exceeded")
            relationships.append((rel_type, resolved.casefold()))
        return relationships

    @staticmethod
    def _verify_text(staged: BinaryIO) -> str:
        data = staged.read(MAX_FILE_BYTES + 1)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise AttachmentValidationError("unsupported_type") from None
        lowered = data.lstrip(b"\xef\xbb\xbf \t\r\n\f").lower()
        if any(lowered.startswith(prefix) for prefix in _ACTIVE_TEXT_PREFIXES):
            raise AttachmentValidationError("active_content")
        if "\0" in text or any(
            ord(character) < 32 and character not in "\t\n\r\f" for character in text
        ):
            raise AttachmentValidationError("unsupported_type")
        return "text/plain"
