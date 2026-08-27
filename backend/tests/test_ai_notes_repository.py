from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.ai_notes.repository import AiNotesContentError, AiNotesRepository


def write_category(root: Path, folder: str, title: str, slug: str) -> Path:
    category = root / folder
    category.mkdir()
    (category / "_index.md").write_text(
        f"---\ntitle: {title}\nslug: {slug}\n---\n", encoding="utf-8"
    )
    return category


def write_article(
    category: Path,
    filename: str,
    *,
    slug: str,
    draft: bool = False,
    title: str = "Agent 系统手册",
    body: str = "# 正文\n\n内容。\n",
) -> None:
    category.joinpath(filename).write_text(
        "---\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        "description: 从原理到实践。\n"
        "publishedAt: 2026-08-27\n"
        "updatedAt: 2026-08-27\n"
        "tags: [Agent, 架构]\n"
        f"draft: {'true' if draft else 'false'}\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )


def test_builds_ordered_published_index_and_whitelist(tmp_path: Path) -> None:
    second = write_category(tmp_path, "02-tools", "工具与框架", "tools")
    first = write_category(tmp_path, "01-foundations", "基础与原理", "foundations")
    write_article(first, "02-second.md", slug="second", title="第二篇")
    write_article(first, "01-first.md", slug="first", title="第一篇")
    write_article(second, "01-draft.md", slug="draft", draft=True)

    repository = AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))

    assert [item.slug for item in repository.index().categories] == [
        "foundations",
        "tools",
    ]
    assert [item.slug for item in repository.index().categories[0].articles] == [
        "first",
        "second",
    ]
    assert repository.index().categories[0].articles[0].filename == "first.md"
    assert repository.article("tools", "draft") is None
    article = repository.article("foundations", "first")
    assert article is not None
    assert article.markdown.startswith("# 正文")
    assert article.reading_minutes == 1


@pytest.mark.parametrize(
    "requested",
    [("../foundations", "first"), ("foundations", "%2e%2e"), ("foundations", "first/extra")],
)
def test_only_prebuilt_valid_slug_keys_can_be_read(
    tmp_path: Path, requested: tuple[str, str]
) -> None:
    category = write_category(
        tmp_path, "01-foundations", "基础与原理", "foundations"
    )
    write_article(category, "01-first.md", slug="first")
    repository = AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))

    assert repository.article(*requested) is None


def test_duplicate_article_slug_is_a_generic_content_error(tmp_path: Path) -> None:
    category = write_category(
        tmp_path, "01-foundations", "基础与原理", "foundations"
    )
    write_article(category, "01-first.md", slug="same")
    write_article(category, "02-second.md", slug="same")

    with pytest.raises(
        AiNotesContentError, match="^AI notes content unavailable$"
    ):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


def test_duplicate_category_slug_is_rejected(tmp_path: Path) -> None:
    write_category(tmp_path, "01-first", "第一类", "same")
    write_category(tmp_path, "02-second", "第二类", "same")

    with pytest.raises(AiNotesContentError):
        AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


def test_reading_minutes_are_derived_from_body_size(tmp_path: Path) -> None:
    category = write_category(
        tmp_path, "01-foundations", "基础与原理", "foundations"
    )
    write_article(category, "01-long.md", slug="long", body="字" * 501)

    repository = AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))

    article = repository.article("foundations", "long")
    assert article is not None
    assert article.reading_minutes == 2
