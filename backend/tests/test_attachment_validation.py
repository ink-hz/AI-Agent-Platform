from __future__ import annotations

import hashlib
import io
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
