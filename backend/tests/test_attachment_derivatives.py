from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from app.attachments.derivatives import (
    DerivativeBuilder,
    DerivativeError,
)
from app.attachments.validation import OpenedObject

FIXTURES = Path(__file__).parent / "fixtures" / "conversation_attachments"


def opened(name: str) -> OpenedObject:
    data = (FIXTURES / name).read_bytes()
    return OpenedObject(io.BytesIO(data), len(data))


@pytest.mark.parametrize("name", ["valid.png", "valid.jpg"])
def test_images_are_reencoded_as_bounded_png_thumbnails(name: str) -> None:
    derivatives = DerivativeBuilder().build(
        opened(name), "image/png" if name.endswith("png") else "image/jpeg"
    )

    assert len(derivatives) == 1
    thumbnail = derivatives[0]
    assert thumbnail.kind == "thumbnail"
    assert thumbnail.detected_mime == "image/png"
    assert thumbnail.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert thumbnail.inline_preview is True
    assert "JFIF" not in thumbnail.data.decode("latin1")


def test_pdf_first_page_uses_absolute_pdftoppm_and_safe_png_reencoding() -> None:
    executable = shutil.which("pdftoppm")
    if executable is None:
        pytest.skip("pdftoppm is not installed")

    derivatives = DerivativeBuilder(pdftoppm_path=executable).build(
        opened("valid.pdf"), "application/pdf"
    )

    assert len(derivatives) == 1
    preview = derivatives[0]
    assert preview.kind == "preview"
    assert preview.detected_mime == "image/png"
    assert preview.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert preview.inline_preview is True


@pytest.mark.parametrize(
    "name",
    ["valid.docx", "valid.xlsx", "valid.pptx"],
)
def test_office_p0_returns_only_safe_coverage_metadata(name: str) -> None:
    mime = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }[Path(name).suffix]

    derivatives = DerivativeBuilder().build(opened(name), mime)

    assert len(derivatives) == 1
    metadata = derivatives[0]
    assert metadata.kind == "preview"
    assert metadata.detected_mime == "application/json"
    assert metadata.inline_preview is False
    assert json.loads(metadata.data) == {
        "coverage": "metadata_only",
        "download": True,
        "inline_preview": False,
    }
    assert b"document.xml" not in metadata.data


def test_derivative_values_redact_content_from_repr() -> None:
    derivative = DerivativeBuilder().build(opened("valid.png"), "image/png")[0]
    assert "PNG" not in repr(derivative)


def test_pdf_renderer_requires_a_fixed_absolute_executable_path() -> None:
    with pytest.raises(ValueError, match="renderer path invalid"):
        DerivativeBuilder(pdftoppm_path="pdftoppm")


def test_truncated_images_never_produce_a_derivative() -> None:
    with pytest.raises(DerivativeError, match="derivative unavailable"):
        DerivativeBuilder().build(opened("truncated.png"), "image/png")
