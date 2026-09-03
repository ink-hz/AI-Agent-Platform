from __future__ import annotations

import hashlib
import io
import struct
import unicodedata
import zipfile
from pathlib import Path

import pytest
from app.attachments.validation import (
    AttachmentValidationError,
    AttachmentValidator,
    OpenedObject,
)

FIXTURES = Path(__file__).parent / "fixtures" / "conversation_attachments"


def opened(name: str) -> OpenedObject:
    data = (FIXTURES / name).read_bytes()
    return OpenedObject(io.BytesIO(data), len(data))


def validate_bytes(data: bytes):
    return AttachmentValidator().validate(
        OpenedObject(io.BytesIO(data), len(data)),
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).digest(),
    )


def docx_with(*entries: tuple[str, bytes], replace: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    replacements = replace or {}
    with zipfile.ZipFile(FIXTURES / "valid.docx") as source, zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            target.writestr(info.filename, replacements.get(info.filename, source.read(info)))
        for name, data in entries:
            target.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize(
    ("name", "detected_mime", "coverage"),
    (
        ("valid.png", "image/png", "safe_thumbnail"),
        ("valid.jpg", "image/jpeg", "safe_thumbnail"),
        ("valid.pdf", "application/pdf", "first_page"),
        ("valid.txt", "text/plain", "metadata_only"),
        (
            "valid.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "metadata_only",
        ),
        (
            "valid.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "metadata_only",
        ),
        (
            "valid.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "metadata_only",
        ),
    ),
)
def test_validation_detects_allowed_content_and_returns_safe_coverage(
    name: str, detected_mime: str, coverage: str
) -> None:
    source = opened(name)
    data = (FIXTURES / name).read_bytes()

    result = AttachmentValidator().validate(
        source,
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).digest(),
    )

    assert result.detected_mime == detected_mime
    assert result.size_bytes == len(data)
    assert result.sha256 == hashlib.sha256(data).digest()
    assert result.coverage["coverage"] == coverage
    assert result.coverage["download"] is True
    assert result.coverage["inline_preview"] is (coverage != "metadata_only")


def test_declared_extension_and_mime_are_not_authoritative() -> None:
    source = opened("extension_mismatch.pdf")
    data = (FIXTURES / "extension_mismatch.pdf").read_bytes()

    result = AttachmentValidator().validate(
        source,
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).digest(),
    )

    assert result.detected_mime == "image/png"
    assert "pdf" not in result.coverage.values()


@pytest.mark.parametrize(
    ("name", "reason"),
    (
        ("truncated.png", "invalid_image"),
        ("zip_bomb.docx", "archive_limits_exceeded"),
        ("encrypted.docx", "encrypted_document"),
        ("local_encrypted.docx", "encrypted_document"),
        ("encrypted_container.docx", "encrypted_document"),
        ("encrypted.pdf", "encrypted_document"),
        ("active.svg", "active_content"),
        ("active.html", "active_content"),
        ("script.sh", "active_content"),
        ("bom_active.html", "active_content"),
        ("polyglot.png", "invalid_image"),
        ("polyglot.jpg", "invalid_image"),
        ("concatenated.jpg", "invalid_image"),
        ("escaped_active.pdf", "active_content"),
        ("external_reference.pdf", "active_content"),
        ("compressed_active.pdf", "active_content"),
        ("movie.pdf", "active_content"),
        ("external_relationship.docx", "active_content"),
        ("application_relationship.docx", "active_content"),
        ("external_connections.xlsx", "active_content"),
        ("macro_content.docx", "active_content"),
        ("activex.docx", "active_content"),
        ("embedding.docx", "active_content"),
        ("traversal.docx", "archive_limits_exceeded"),
        ("untyped_part.docx", "invalid_office"),
        ("polyglot.docx", "invalid_office"),
        ("corrupt_crc.docx", "invalid_office"),
        ("forged_metadata.docx", "archive_limits_exceeded"),
        ("malformed_relationship.docx", "invalid_office"),
    ),
)
def test_validation_rejects_unsafe_content_with_stable_reason(
    name: str, reason: str
) -> None:
    source = opened(name)
    data = (FIXTURES / name).read_bytes()

    with pytest.raises(AttachmentValidationError) as captured:
        AttachmentValidator().validate(
            source,
            expected_size=len(data),
            expected_sha256=hashlib.sha256(data).digest(),
        )

    assert captured.value.reason == reason
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert name not in str(captured.value)
    assert name not in repr(captured.value)


def test_minimal_office_fixtures_have_authoritative_package_roots() -> None:
    """The positive corpus must exercise actual OPC relationships and roots."""
    import zipfile

    expected = {
        "valid.docx": ("word/document.xml", "document"),
        "valid.xlsx": ("xl/workbook.xml", "workbook"),
        "valid.pptx": ("ppt/presentation.xml", "presentation"),
    }
    for name, (part, root) in expected.items():
        with zipfile.ZipFile(FIXTURES / name) as package:
            assert "_rels/.rels" in package.namelist()
            assert part in package.namelist()
            assert root.encode() in package.read(part)


@pytest.mark.parametrize("wrong", ["size", "digest"])
def test_validation_recomputes_complete_size_and_digest(wrong: str) -> None:
    data = (FIXTURES / "valid.txt").read_bytes()
    size = len(data) + (1 if wrong == "size" else 0)
    digest = b"x" * 32 if wrong == "digest" else hashlib.sha256(data).digest()

    with pytest.raises(AttachmentValidationError) as captured:
        AttachmentValidator().validate(
            OpenedObject(io.BytesIO(data), len(data)),
            expected_size=size,
            expected_sha256=digest,
        )

    assert captured.value.reason == "integrity_mismatch"


def test_opened_object_and_results_redact_bytes_from_repr() -> None:
    source = OpenedObject(io.BytesIO(b"candidate-secret"), 16)
    assert "candidate-secret" not in repr(source)


@pytest.mark.parametrize(
    "probe",
    (
        b'<?xml version="1.0"?><!DOCTYPE html><html/>',
        b'\xef\xbb\xbf \n<!-- harmless-looking -->\n<svg/>',
        b'<?xml version="1.0"?>\n<!--x--><script>x()</script>',
    ),
)
def test_active_text_cannot_hide_behind_xml_prolog_comments_or_bom(probe: bytes) -> None:
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(probe)
    assert captured.value.reason == "active_content"


@pytest.mark.parametrize(
    "probe",
    (
        "hello<script>alert(1)</script>",
        "\u00a0\u2003<svg onload='alert(1)'></svg>",
        "prefix<ScRiPt \n type='text/javascript'>x()</sCrIpT>",
        "prefix &lt;ScRiPt&#x20;&gt;x()&lt;/sCrIpT&gt;",
        "prefix &amp;lt;svg onload=x&amp;gt;&amp;lt;/svg&amp;gt;",
        "prefix &amp;amp;amp;lt;script&amp;amp;amp;gt;x()",
    ),
)
def test_active_markup_anywhere_or_entity_encoded_is_rejected(probe: str) -> None:
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(probe.encode("utf-8"))
    assert captured.value.reason == "active_content"


@pytest.mark.parametrize(
    "plain_text",
    (
        "Use 1 < 2 and 3 > 1 in this policy.\n",
        "A generic vector<T> is plain source documentation.\n",
        "The budget is < USD 100 and contains no markup.\n",
    ),
)
def test_plain_text_with_non_markup_less_than_sign_remains_allowed(
    plain_text: str,
) -> None:
    assert validate_bytes(plain_text.encode("utf-8")).detected_mime == "text/plain"


@pytest.mark.parametrize(
    "probe",
    (
        "<x onclick=alert(1)>click</x>",
        "<x-evil onmouseover=alert(1)>click</x-evil>",
        "<widget STYLE='background:url(https://example.invalid)'>x</widget>",
        "<widget src='https://example.invalid'>x</widget>",
        "<widget href='https://example.invalid'>x</widget>",
        "<widget srcdoc='<script>x()</script>'>x</widget>",
        "<widget action='https://example.invalid'>x</widget>",
        "<widget formaction='https://example.invalid'>x</widget>",
        "<employee-record>data</employee-record>",
        "<employee-record />",
        "&lt;x-evil&#x20;OnClick=alert(1)&gt;click&lt;/x-evil&gt;",
        "&amp;lt;x-evil onmouseover=alert(1)&amp;gt;x&amp;lt;/x-evil&amp;gt;",
    ),
)
def test_custom_markup_and_dangerous_attributes_are_rejected(probe: str) -> None:
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(probe.encode("utf-8"))
    assert captured.value.reason == "active_content"


@pytest.mark.parametrize(
    "probe",
    (
        '<x title="<" onclick=alert(1)>',
        'prefix<img alt="<" src=x onerror=alert(1)>',
        '<x title="&lt;" onclick=alert(1)>',
        "<x title='<' onfocus=alert(1)>",
        "&lt;x title=&quot;&lt;&quot; onclick=alert(1)&gt;",
        "&amp;lt;x title=&amp;quot;&amp;lt;&amp;quot; onclick=alert(1)&amp;gt;",
        '<x title="unterminated',
        '<x title="<" onclick=alert(1)',
    ),
)
def test_quote_aware_markup_detection_rejects_active_or_malformed_tags(
    probe: str,
) -> None:
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(probe.encode("utf-8"))
    assert captured.value.reason == "active_content"


def test_office_rejects_an_undeclared_gap_before_central_directory() -> None:
    candidate = bytearray((FIXTURES / "valid.docx").read_bytes())
    eocd = candidate.rfind(b"PK\x05\x06")
    central_offset = struct.unpack_from("<I", candidate, eocd + 16)[0]
    candidate[central_offset:central_offset] = b"UNDECLARED-GAP"
    eocd += len(b"UNDECLARED-GAP")
    struct.pack_into("<I", candidate, eocd + 16, central_offset + len(b"UNDECLARED-GAP"))
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(bytes(candidate))
    assert captured.value.reason == "invalid_office"


def test_office_rejects_local_and_central_timestamp_mismatch() -> None:
    candidate = bytearray((FIXTURES / "valid.docx").read_bytes())
    local = candidate.find(b"PK\x03\x04")
    original_date = struct.unpack_from("<H", candidate, local + 12)[0]
    struct.pack_into("<H", candidate, local + 12, original_date ^ 1)
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(bytes(candidate))
    assert captured.value.reason == "invalid_office"


@pytest.mark.parametrize(
    "name",
    (
        "C:/word/hidden.xml",
        "word/control\x01.xml",
        "word/Styles.xml",
        unicodedata.normalize("NFD", "word/caf\xe9.xml"),
    ),
)
def test_office_rejects_ambiguous_or_noncanonical_part_paths(name: str) -> None:
    entries = [(name, b"<x/>")]
    if name == "word/Styles.xml":
        entries.append(("word/styles.xml", b"<x/>"))
    with pytest.raises(AttachmentValidationError):
        validate_bytes(docx_with(*entries))


def test_office_rejects_unsupported_hidden_binary_even_with_xml_extension() -> None:
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(docx_with(("word/hidden.xml", b"MZ\x90\x00candidate")))
    assert captured.value.reason == "invalid_office"


def test_office_parses_every_allowed_xml_part() -> None:
    content_types = (
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        b'<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        b'</Types>'
    )
    candidate = docx_with(
        ("word/styles.xml", b"<candidate-secret"),
        replace={"[Content_Types].xml": content_types},
    )
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(candidate)
    assert captured.value.reason == "invalid_office"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_parser_failure_chain_never_contains_candidate_content() -> None:
    candidate = b"%PDF-1.7\ncandidate-object-ref\n%%EOF\n"
    with pytest.raises(AttachmentValidationError) as captured:
        validate_bytes(candidate)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "candidate-object-ref" not in repr(captured.value)
