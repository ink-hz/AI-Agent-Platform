from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.ai_notes.repository import AiNotesContentError, AiNotesRepository
from app.ai_notes.validation import validate_publication
from test_ai_notes_repository import write_article, write_category


def test_rejects_unknown_files(tmp_path: Path) -> None:
    category = write_category(
        tmp_path, "01-foundations", "基础与原理", "foundations"
    )
    (category / "asset.png").write_bytes(b"not allowed")

    with pytest.raises(AiNotesContentError):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


def test_rejects_article_symlink(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    category = write_category(
        content, "01-foundations", "基础与原理", "foundations"
    )
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    category.joinpath("01-link.md").symlink_to(outside)

    with pytest.raises(AiNotesContentError):
        AiNotesRepository.load(content, today=date(2026, 8, 27))


@pytest.mark.parametrize(
    ("published", "updated"),
    [
        ("2026-05-24", "2026-05-24"),
        ("2026-08-28", "2026-08-28"),
        ("2026-08-27", "2026-08-26"),
    ],
)
def test_rejects_invalid_publication_dates(
    tmp_path: Path, published: str, updated: str
) -> None:
    category = write_category(
        tmp_path, "01-foundations", "基础与原理", "foundations"
    )
    write_article(category, "01-invalid.md", slug="invalid")
    path = category / "01-invalid.md"
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "publishedAt: 2026-08-27", f"publishedAt: {published}"
    )
    source = source.replace("updatedAt: 2026-08-27", f"updatedAt: {updated}")
    path.write_text(source, encoding="utf-8")

    with pytest.raises(AiNotesContentError):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


@pytest.mark.parametrize(
    "link",
    [
        "[x](javascript:alert(1))",
        "[x](DATA:text/html,bad)",
        "[x][unsafe]\n\n[unsafe]: vbscript:msgbox(1)",
        "<java&#x73;cript:alert(1)>",
    ],
)
def test_publication_rejects_dangerous_links(tmp_path: Path, link: str) -> None:
    content = tmp_path / "content"
    content.mkdir()
    category = write_category(
        content, "01-foundations", "基础与原理", "foundations"
    )
    write_article(category, "01-first.md", slug="first", body=f"# 正文\n\n{link}\n")
    markers = tmp_path / "markers.yaml"
    markers.write_text("markers: []\n", encoding="utf-8")

    with pytest.raises(AiNotesContentError):
        validate_publication(content, markers, today=date(2026, 8, 27))


def test_publication_rejects_markers_without_leaking_values(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    category = write_category(
        content, "01-foundations", "基础与原理", "foundations"
    )
    write_article(
        category,
        "01-first.md",
        slug="first",
        body="# 正文\n\n包含旧组织代号。\n",
    )
    markers = tmp_path / "markers.yaml"
    markers.write_text("markers:\n  - 旧组织代号\n", encoding="utf-8")

    with pytest.raises(AiNotesContentError) as raised:
        validate_publication(content, markers, today=date(2026, 8, 27))

    assert str(raised.value) == "AI notes content unavailable"
    assert "旧组织代号" not in str(raised.value)
    assert str(tmp_path) not in str(raised.value)


def test_draft_markers_do_not_block_publication_but_are_structurally_valid(
    tmp_path: Path,
) -> None:
    content = tmp_path / "content"
    content.mkdir()
    category = write_category(
        content, "01-foundations", "基础与原理", "foundations"
    )
    write_article(
        category,
        "01-draft.md",
        slug="draft",
        draft=True,
        body="# 草稿\n\n旧组织代号\n",
    )
    markers = tmp_path / "markers.yaml"
    markers.write_text("markers:\n  - 旧组织代号\n", encoding="utf-8")

    index = validate_publication(content, markers, today=date(2026, 8, 27))

    assert index.categories[0].articles == ()


def test_publication_accepts_internal_and_https_links(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    category = write_category(
        content, "01-foundations", "基础与原理", "foundations"
    )
    write_article(
        category,
        "01-first.md",
        slug="first",
        body="# 正文\n\n[章节](#section) [站内](/ai-notes) [官方](https://example.com)\n",
    )
    markers = tmp_path / "markers.yaml"
    markers.write_text("markers: []\n", encoding="utf-8")

    index = validate_publication(content, markers, today=date(2026, 8, 27))

    assert index.categories[0].articles[0].slug == "first"
