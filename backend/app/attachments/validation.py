from __future__ import annotations

import hashlib
import html as html_lib
import io
import posixpath
import re
import struct
import tempfile
import unicodedata
import urllib.parse
import warnings
import zipfile
from collections.abc import Callable, Iterator, Mapping
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
_MAX_XML_NODES = 20_000
_MAX_XML_DEPTH = 64
_MAX_XML_TEXT_BYTES = 10 * 1024 * 1024
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
_ACTIVE_TEXT_PREFIXES = (
    b"#!",
    b"<!doctype html",
    b"<html",
    b"<svg",
    b"<script",
    b"<?php",
)
_MARKUP_TAG = re.compile(
    r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9_.:-]*)([^<>]*?)(/?)>", re.DOTALL
)
_DANGEROUS_MARKUP_ATTRIBUTE = re.compile(
    r"(?:^|\s)(?:on[A-Za-z0-9_.:-]+|style|src|href|srcdoc|action|formaction)"
    r"(?=\s*=|\s|/|$)",
    re.IGNORECASE,
)
_MARKUP_DECLARATION = re.compile(
    r"<\s*(?:!\s*(?:doctype|entity)\b|\?\s*(?:xml|php)\b)",
    re.IGNORECASE,
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
_REL_PREFIX = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_COMMON_RELATIONSHIP_KINDS = {
    "core-properties",
    "extended-properties",
    "custom-properties",
    "theme",
    "image",
}
_FORMAT_RELATIONSHIP_KINDS = {
    "word": _COMMON_RELATIONSHIP_KINDS
    | {"officeDocument", "styles", "settings", "fontTable", "numbering", "header", "footer", "footnotes", "endnotes", "comments"},
    "xl": _COMMON_RELATIONSHIP_KINDS
    | {"officeDocument", "worksheet", "styles", "sharedStrings", "table"},
    "ppt": _COMMON_RELATIONSHIP_KINDS
    | {"officeDocument", "slide", "slideMaster", "slideLayout", "notesSlide", "notesMaster", "presProps", "viewProps", "tableStyles"},
}
_KNOWN_ACTIVE_OFFICE_TOKENS = (
    "activex",
    "connections",
    "customui",
    "externaldata",
    "externallink",
    "embeddings/",
    "oleobject",
    "vbaproject",
)
_COMMON_PARTS: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"docProps/core\.xml"), frozenset({"application/vnd.openxmlformats-package.core-properties+xml"})),
    (re.compile(r"docProps/app\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.extended-properties+xml"})),
    (re.compile(r"docProps/custom\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.custom-properties+xml"})),
)
_FORMAT_PARTS: dict[str, tuple[tuple[re.Pattern[str], frozenset[str]], ...]] = {
    "word": (
        (re.compile(r"word/document\.xml"), frozenset({_OFFICE_TYPES[0][1]})),
        (re.compile(r"word/(styles|settings|fontTable|numbering|footnotes|endnotes|comments)\.xml"), frozenset({
            "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        })),
        (re.compile(r"word/(header|footer)[1-9][0-9]*\.xml"), frozenset({
            "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
        })),
        (re.compile(r"word/theme/theme[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.theme+xml"})),
    ),
    "xl": (
        (re.compile(r"xl/workbook\.xml"), frozenset({_OFFICE_TYPES[1][1]})),
        (re.compile(r"xl/(styles|sharedStrings)\.xml"), frozenset({
            "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
        })),
        (re.compile(r"xl/worksheets/sheet[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})),
        (re.compile(r"xl/tables/table[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"})),
        (re.compile(r"xl/theme/theme[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.theme+xml"})),
    ),
    "ppt": (
        (re.compile(r"ppt/presentation\.xml"), frozenset({_OFFICE_TYPES[2][1]})),
        (re.compile(r"ppt/(presProps|viewProps|tableStyles)\.xml"), frozenset({
            "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml",
            "application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml",
            "application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml",
        })),
        (re.compile(r"ppt/slides/slide[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.presentationml.slide+xml"})),
        (re.compile(r"ppt/slideMasters/slideMaster[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"})),
        (re.compile(r"ppt/slideLayouts/slideLayout[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"})),
        (re.compile(r"ppt/theme/theme[1-9][0-9]*\.xml"), frozenset({"application/vnd.openxmlformats-officedocument.theme+xml"})),
    ),
}


class AttachmentValidationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("attachment validation rejected")


@dataclass(repr=False)
class OpenedObject:
    stream: BinaryIO = field(repr=False)
    size: int
    immutable_locator: str | None = field(default=None, repr=False)
    set_read_timeout: Callable[[float], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 0
            or not hasattr(self.stream, "read")
            or (
                self.immutable_locator is not None
                and (
                    not isinstance(self.immutable_locator, str)
                    or not re.fullmatch(r"(?:version|etag):[^\x00-\x20\x7f]{1,1000}", self.immutable_locator)
                )
            )
            or (self.set_read_timeout is not None and not callable(self.set_read_timeout))
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

    def iter_chunks_until(
        self,
        deadline: float,
        monotonic: Callable[[], float],
        chunk_bytes: int = _CHUNK_BYTES,
    ) -> Iterator[bytes]:
        if self.set_read_timeout is None and not isinstance(self.stream, io.BytesIO):
            raise AttachmentValidationError("invalid_stream")
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AttachmentValidationError("invalid_stream")
            if self.set_read_timeout is not None:
                self.set_read_timeout(remaining)
            chunk = self.stream.read(chunk_bytes)
            if deadline - monotonic() <= 0 or not isinstance(chunk, bytes):
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
    immutable_locator: str | None = field(default=None, repr=False)


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
        return ValidationResult(
            detected_mime, size, checksum, coverage, source.immutable_locator
        )

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
        parser_failed = False
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
            parser_failed = True
        if parser_failed:
            raise AttachmentValidationError("invalid_image")

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
        parser_failed = False
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
            parser_failed = True
        if parser_failed:
            raise AttachmentValidationError("invalid_document")

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
                resolution_failed = False
                try:
                    value = value.get_object()
                except Exception:  # noqa: BLE001 - parser details are sensitive
                    resolution_failed = True
                if resolution_failed:
                    raise AttachmentValidationError("invalid_document")
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
        parser_failed = False
        try:
            central_offset = AttachmentValidator._zip_layout(staged)
            if central_offset is None:
                raise AttachmentValidationError("invalid_office")
            staged.seek(0)
            with zipfile.ZipFile(staged) as archive:
                entries = archive.infolist()
                if not entries or len(entries) > _MAX_OFFICE_ENTRIES:
                    raise AttachmentValidationError("archive_limits_exceeded")
                total = 0
                seen: set[str] = set()
                for entry in entries:
                    normalized = entry.filename
                    folded = normalized.casefold()
                    total += entry.file_size
                    ratio = entry.file_size / max(1, entry.compress_size)
                    if (
                        entry.flag_bits & 1
                        or folded in seen
                        or normalized.startswith("/")
                        or "\\" in entry.filename
                        or ":" in normalized
                        or unicodedata.normalize("NFC", normalized) != normalized
                        or any(unicodedata.category(character).startswith("C") for character in normalized)
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
                    seen.add(folded)
                AttachmentValidator._verify_zip_local_headers(
                    staged, entries, central_offset=central_offset
                )
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
                    if not content_type:
                        raise AttachmentValidationError("invalid_office")
                    if any(token in content_type.casefold() for token in _KNOWN_ACTIVE_OFFICE_TOKENS):
                        raise AttachmentValidationError("active_content")
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

                if "_rels/.rels" not in seen:
                    raise AttachmentValidationError("invalid_office")
                package_rels = AttachmentValidator._relationships(
                    archive.read(next(e.filename for e in entries if e.filename.casefold() == "_rels/.rels")),
                    base="",
                    names=seen,
                    allowed_kinds={"officeDocument", "core-properties", "extended-properties", "custom-properties"},
                )
                roots = [target for rel_type, target in package_rels if rel_type == _OFFICE_DOCUMENT_REL]
                if len(roots) != 1:
                    raise AttachmentValidationError("invalid_office")
                selected_mime = None
                selected_format = None
                for root_name, marker, mime, root_tag in _OFFICE_TYPES:
                    if roots[0].casefold() == root_name and overrides.get(root_name) == marker:
                        root = AttachmentValidator._safe_xml(
                            archive.read(next(e.filename for e in entries if e.filename.casefold() == root_name))
                        )
                        if root.tag != root_tag:
                            raise AttachmentValidationError("invalid_office")
                        selected_mime = mime
                        selected_format = root_name.split("/", 1)[0]
                        break
                if selected_mime is None or selected_format is None:
                    raise AttachmentValidationError("invalid_office")

                policies = _COMMON_PARTS + _FORMAT_PARTS[selected_format]
                for entry in entries:
                    name = entry.filename
                    folded_name = name.casefold()
                    if entry.is_dir():
                        raise AttachmentValidationError("invalid_office")
                    if folded_name in {"[content_types].xml", "_rels/.rels"}:
                        continue
                    if folded_name.endswith(".rels"):
                        if not re.fullmatch(
                            rf"(?:{selected_format}/)?(?:.+/)?_rels/[^/]+\.rels",
                            name,
                        ):
                            raise AttachmentValidationError("invalid_office")
                        parent = name
                        rel_dir = posixpath.dirname(posixpath.dirname(parent))
                        source_name = posixpath.join(
                            rel_dir,
                            posixpath.basename(parent).removesuffix(".rels"),
                        ).casefold()
                        if source_name not in seen:
                            raise AttachmentValidationError("invalid_office")
                        extension = folded_name.rsplit(".", 1)[-1]
                        assigned_type = overrides.get(folded_name, defaults.get(extension))
                        if assigned_type != "application/vnd.openxmlformats-package.relationships+xml":
                            raise AttachmentValidationError("invalid_office")
                        AttachmentValidator._relationships(
                            archive.read(entry),
                            base=rel_dir,
                            names=seen,
                            allowed_kinds=_FORMAT_RELATIONSHIP_KINDS[selected_format],
                        )
                        continue
                    assigned_type = overrides.get(folded_name)
                    if assigned_type is None:
                        extension = folded_name.rsplit(".", 1)[-1] if "." in folded_name else ""
                        assigned_type = defaults.get(extension)
                    if re.fullmatch(
                        rf"{selected_format}/media/image[1-9][0-9]*\.(png|jpe?g)",
                        name,
                    ):
                        expected = "image/png" if name.endswith(".png") else "image/jpeg"
                        if assigned_type != expected:
                            raise AttachmentValidationError("invalid_office")
                        media = archive.read(entry)
                        with io.BytesIO(media) as stream:
                            AttachmentValidator._verify_image(
                                stream,
                                "PNG" if expected == "image/png" else "JPEG",
                                len(media),
                            )
                        continue
                    if assigned_type is None or not any(
                        pattern.fullmatch(name) and assigned_type in content_types
                        for pattern, content_types in policies
                    ):
                        if any(token in folded_name for token in _KNOWN_ACTIVE_OFFICE_TOKENS):
                            raise AttachmentValidationError("active_content")
                        raise AttachmentValidationError("invalid_office")
                    if name.endswith(".xml"):
                        AttachmentValidator._safe_xml(archive.read(entry))
                for entry in entries:
                    with archive.open(entry) as member:
                        while member.read(_CHUNK_BYTES):
                            pass
                return selected_mime
        except AttachmentValidationError:
            raise
        except Exception:  # noqa: BLE001 - package parser details are sensitive
            parser_failed = True
        if parser_failed:
            raise AttachmentValidationError("invalid_office")

    @staticmethod
    def _verify_zip_local_headers(
        staged: BinaryIO,
        entries: list[zipfile.ZipInfo],
        *,
        central_offset: int,
    ) -> None:
        ordered = sorted(entries, key=lambda value: value.header_offset)
        if not ordered or ordered[0].header_offset != 0:
            raise AttachmentValidationError("invalid_office")
        for index, entry in enumerate(ordered):
            staged.seek(entry.header_offset)
            header = staged.read(30)
            if len(header) != 30 or header[:4] != b"PK\x03\x04":
                raise AttachmentValidationError("invalid_office")
            (
                extract_version,
                flags,
                method,
                modified_time,
                modified_date,
                crc,
                compressed_size,
                file_size,
                name_length,
                extra_length,
            ) = (
                struct.unpack_from("<H", header, 4)[0],
                struct.unpack_from("<H", header, 6)[0],
                struct.unpack_from("<H", header, 8)[0],
                struct.unpack_from("<H", header, 10)[0],
                struct.unpack_from("<H", header, 12)[0],
                struct.unpack_from("<I", header, 14)[0],
                struct.unpack_from("<I", header, 18)[0],
                struct.unpack_from("<I", header, 22)[0],
                struct.unpack_from("<H", header, 26)[0],
                struct.unpack_from("<H", header, 28)[0],
            )
            local_name = staged.read(name_length)
            name_failed = False
            try:
                decoded_name = local_name.decode(
                    "utf-8" if flags & 0x800 else "cp437"
                )
            except UnicodeDecodeError:
                name_failed = True
            if name_failed:
                raise AttachmentValidationError("invalid_office")
            if flags != entry.flag_bits:
                reason = "encrypted_document" if (flags | entry.flag_bits) & 1 else "invalid_office"
                raise AttachmentValidationError(reason)
            if flags & 1:
                raise AttachmentValidationError("encrypted_document")
            year, month, day, hour, minute, second = entry.date_time
            expected_date = ((year - 1980) << 9) | (month << 5) | day
            expected_time = (hour << 11) | (minute << 5) | (second // 2)
            if (
                extract_version != entry.extract_version
                or flags & ~(0x0800 | 0x0008)
                or method != entry.compress_type
                or method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or decoded_name != entry.filename
                or extra_length != 0
                or modified_date != expected_date
                or modified_time != expected_time
            ):
                raise AttachmentValidationError("invalid_office")
            if not flags & 0x08 and (
                crc != entry.CRC
                or compressed_size != entry.compress_size
                or file_size != entry.file_size
            ):
                raise AttachmentValidationError("invalid_office")
            data_end = entry.header_offset + 30 + name_length + extra_length + entry.compress_size
            next_offset = (
                ordered[index + 1].header_offset
                if index + 1 < len(ordered)
                else central_offset
            )
            if flags & 0x08:
                staged.seek(data_end)
                descriptor = staged.read(next_offset - data_end)
                if descriptor.startswith(b"PK\x07\x08"):
                    descriptor = descriptor[4:]
                if len(descriptor) != 12 or struct.unpack("<III", descriptor) != (
                    entry.CRC,
                    entry.compress_size,
                    entry.file_size,
                ):
                    raise AttachmentValidationError("invalid_office")
            elif data_end != next_offset:
                raise AttachmentValidationError("invalid_office")

    @staticmethod
    def _zip_layout(staged: BinaryIO) -> int | None:
        staged.seek(0, 2)
        size = staged.tell()
        if size < 22:
            return None
        staged.seek(max(0, size - 65_557))
        tail = staged.read()
        marker = tail.rfind(b"PK\x05\x06")
        if marker < 0 or marker + 22 > len(tail):
            return None
        comment_length = int.from_bytes(tail[marker + 20 : marker + 22], "little")
        eocd_offset = size - len(tail) + marker
        if comment_length != 0 or marker + 22 != len(tail):
            return None
        disk, central_disk, disk_entries, total_entries = struct.unpack_from("<HHHH", tail, marker + 4)
        central_size, central_offset = struct.unpack_from("<II", tail, marker + 12)
        if disk or central_disk or disk_entries != total_entries or total_entries > _MAX_OFFICE_ENTRIES:
            return None
        if central_offset + central_size != eocd_offset:
            return None
        staged.seek(central_offset)
        remaining = central_size
        parsed = 0
        while remaining:
            header = staged.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                return None
            variable = sum(struct.unpack_from("<HHH", header, 28))
            extra_length = struct.unpack_from("<H", header, 30)[0]
            file_comment_length = struct.unpack_from("<H", header, 32)[0]
            if extra_length or file_comment_length:
                return None
            if 46 + variable > remaining:
                return None
            staged.seek(variable, 1)
            remaining -= 46 + variable
            parsed += 1
        return central_offset if parsed == total_entries else None

    @staticmethod
    def _safe_xml(data: bytes):
        if len(data) > _MAX_OFFICE_ENTRY_BYTES:
            raise AttachmentValidationError("archive_limits_exceeded")
        failed = False
        try:
            root = SafeElementTree.fromstring(data)
        except (DefusedXmlException, SafeElementTree.ParseError, ValueError):
            failed = True
        if failed:
            raise AttachmentValidationError("invalid_office")
        pending = [(root, 0)]
        nodes = 0
        text_bytes = 0
        while pending:
            node, depth = pending.pop()
            nodes += 1
            if depth > _MAX_XML_DEPTH or nodes > _MAX_XML_NODES:
                raise AttachmentValidationError("archive_limits_exceeded")
            text_bytes += len((node.text or "").encode("utf-8"))
            text_bytes += len((node.tail or "").encode("utf-8"))
            text_bytes += sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in node.attrib.items())
            if text_bytes > _MAX_XML_TEXT_BYTES:
                raise AttachmentValidationError("archive_limits_exceeded")
            pending.extend((child, depth + 1) for child in node)
        return root

    @staticmethod
    def _relationships(
        data: bytes,
        *,
        base: str,
        names: set[str],
        allowed_kinds: set[str],
    ) -> list[tuple[str, str]]:
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
            ):
                reason = "active_content" if mode != "Internal" else "invalid_office"
                raise AttachmentValidationError(reason)
            if rel_type == _OFFICE_DOCUMENT_REL:
                kind = "officeDocument"
            elif rel_type.startswith(_REL_PREFIX):
                kind = rel_type.removeprefix(_REL_PREFIX)
            elif rel_type in {
                "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties",
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties",
            }:
                kind = rel_type.rsplit("/", 1)[-1]
            else:
                raise AttachmentValidationError("active_content")
            if kind not in allowed_kinds:
                raise AttachmentValidationError("active_content")
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
        decode_failed = False
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            decode_failed = True
        if decode_failed:
            raise AttachmentValidationError("unsupported_type")
        probe = text.lstrip("\ufeff \t\r\n\f")
        for _ in range(32):
            lowered = probe.casefold()
            if lowered.startswith("<?xml"):
                end = probe.find("?>")
                if end < 0:
                    raise AttachmentValidationError("active_content")
                probe = probe[end + 2 :].lstrip(" \t\r\n\f")
                continue
            if lowered.startswith("<!--"):
                end = probe.find("-->")
                if end < 0:
                    raise AttachmentValidationError("active_content")
                probe = probe[end + 3 :].lstrip(" \t\r\n\f")
                continue
            break
        lowered = probe.casefold().encode("utf-8")
        if any(lowered.startswith(prefix) for prefix in _ACTIVE_TEXT_PREFIXES):
            raise AttachmentValidationError("active_content")
        normalized = text
        for _ in range(3):
            decoded = html_lib.unescape(normalized)
            if decoded == normalized:
                break
            normalized = decoded
        else:
            if html_lib.unescape(normalized) != normalized:
                raise AttachmentValidationError("active_content")
        if _MARKUP_DECLARATION.search(normalized):
            raise AttachmentValidationError("active_content")
        for match in _MARKUP_TAG.finditer(normalized):
            closing, name, attributes, self_closing = match.groups()
            generic_type_parameter = (
                not closing
                and not self_closing
                and not attributes.strip()
                and len(name) == 1
                and name.isascii()
                and name.isupper()
                and match.start() > 0
                and normalized[match.start() - 1].isascii()
                and normalized[match.start() - 1].isalnum()
            )
            if (
                closing
                or self_closing
                or _DANGEROUS_MARKUP_ATTRIBUTE.search(attributes)
                or not generic_type_parameter
            ):
                raise AttachmentValidationError("active_content")
        if "\0" in text or any(
            ord(character) < 32 and character not in "\t\n\r\f" for character in text
        ):
            raise AttachmentValidationError("unsupported_type")
        return "text/plain"
