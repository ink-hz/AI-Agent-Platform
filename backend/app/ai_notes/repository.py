from __future__ import annotations

from collections.abc import Iterator
from datetime import date
import math
from pathlib import Path
import re
from typing import Any

import yaml

from .models import (
    AiNoteArticle,
    AiNoteCategory,
    AiNoteSummary,
    AiNotesIndex,
    ArticleFrontmatter,
    CategoryFrontmatter,
)


CATEGORY_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
ARTICLE_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_CATEGORY_FOLDER = re.compile(r"(?P<order>[0-9]+)-[a-z0-9][a-z0-9-]*\Z")
_ARTICLE_FILE = re.compile(
    r"(?P<order>[0-9]+)-(?P<display>[a-z0-9][a-z0-9-]*\.md)\Z"
)
_FRONTMATTER = re.compile(
    r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)
_FIRST_PUBLICATION_DATE = date(2026, 5, 25)


class AiNotesContentError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("AI notes content unavailable")


class AiNotesRepository:
    def __init__(
        self,
        index: AiNotesIndex,
        articles: dict[tuple[str, str], AiNoteArticle],
    ) -> None:
        self._index = index
        self._articles = articles

    @classmethod
    def load(cls, root: Path, *, today: date) -> "AiNotesRepository":
        try:
            return _load_repository(root, today=today)
        except AiNotesContentError:
            raise
        except Exception:
            raise AiNotesContentError() from None

    def index(self) -> AiNotesIndex:
        return self._index

    def article(
        self, category_slug: str, article_slug: str
    ) -> AiNoteArticle | None:
        if (
            CATEGORY_SLUG.fullmatch(category_slug) is None
            or ARTICLE_SLUG.fullmatch(article_slug) is None
        ):
            return None
        return self._articles.get((category_slug, article_slug))


def reading_minutes(markdown: str) -> int:
    visible_units = len(re.sub(r"\s+", "", markdown))
    return max(1, math.ceil(visible_units / 500))


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise AiNotesContentError()
    source = path.read_text(encoding="utf-8")
    if source.startswith("\ufeff"):
        raise AiNotesContentError()
    matched = _FRONTMATTER.fullmatch(source)
    if matched is None:
        raise AiNotesContentError()
    metadata = yaml.safe_load(matched.group("yaml"))
    if not isinstance(metadata, dict):
        raise AiNotesContentError()
    return metadata, matched.group("body").lstrip("\r\n")


def iter_validated_articles(
    root: Path, *, today: date
) -> Iterator[tuple[Path, ArticleFrontmatter, str]]:
    for category_path in _category_paths(root):
        for article_path in _article_paths(category_path):
            metadata, markdown = parse_frontmatter(article_path)
            frontmatter = ArticleFrontmatter.model_validate(metadata)
            _validate_article_dates(frontmatter, today=today)
            yield article_path, frontmatter, markdown


def _load_repository(root: Path, *, today: date) -> AiNotesRepository:
    categories: list[AiNoteCategory] = []
    articles: dict[tuple[str, str], AiNoteArticle] = {}
    category_slugs: set[str] = set()

    for category_path in _category_paths(root):
        category_metadata, category_body = parse_frontmatter(
            category_path / "_index.md"
        )
        if category_body.strip():
            raise AiNotesContentError()
        category = CategoryFrontmatter.model_validate(category_metadata)
        if category.slug in category_slugs:
            raise AiNotesContentError()
        category_slugs.add(category.slug)

        summaries: list[AiNoteSummary] = []
        article_slugs: set[str] = set()
        for article_path in _article_paths(category_path):
            metadata, markdown = parse_frontmatter(article_path)
            frontmatter = ArticleFrontmatter.model_validate(metadata)
            _validate_article_dates(frontmatter, today=today)
            if frontmatter.slug in article_slugs:
                raise AiNotesContentError()
            article_slugs.add(frontmatter.slug)
            if frontmatter.draft:
                continue

            matched = _ARTICLE_FILE.fullmatch(article_path.name)
            if matched is None:
                raise AiNotesContentError()
            summary = AiNoteSummary(
                slug=frontmatter.slug,
                title=frontmatter.title,
                filename=matched.group("display"),
                description=frontmatter.description,
                author=frontmatter.author,
                motto=frontmatter.motto,
                published_at=frontmatter.published_at,
                updated_at=frontmatter.updated_at,
                tags=frontmatter.tags,
                reading_minutes=reading_minutes(markdown),
            )
            summaries.append(summary)
            article = AiNoteArticle(
                **summary.model_dump(),
                category_slug=category.slug,
                category_title=category.title,
                markdown=markdown,
            )
            articles[(category.slug, frontmatter.slug)] = article

        categories.append(
            AiNoteCategory(
                slug=category.slug,
                title=category.title,
                articles=tuple(summaries),
            )
        )

    return AiNotesRepository(AiNotesIndex(categories=tuple(categories)), articles)


def _category_paths(root: Path) -> tuple[Path, ...]:
    if root.is_symlink() or not root.is_dir():
        raise AiNotesContentError()
    selected: list[tuple[int, str, Path]] = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir():
            raise AiNotesContentError()
        matched = _CATEGORY_FOLDER.fullmatch(path.name)
        if matched is None:
            raise AiNotesContentError()
        selected.append((int(matched.group("order")), path.name, path))
    selected.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in selected)


def _article_paths(category_path: Path) -> tuple[Path, ...]:
    index_path = category_path / "_index.md"
    if index_path.is_symlink() or not index_path.is_file():
        raise AiNotesContentError()
    selected: list[tuple[int, str, Path]] = []
    for path in category_path.iterdir():
        if path.name == "_index.md":
            continue
        if path.is_symlink() or not path.is_file():
            raise AiNotesContentError()
        matched = _ARTICLE_FILE.fullmatch(path.name)
        if matched is None:
            raise AiNotesContentError()
        selected.append((int(matched.group("order")), path.name, path))
    selected.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in selected)


def _validate_article_dates(frontmatter: ArticleFrontmatter, *, today: date) -> None:
    if not _FIRST_PUBLICATION_DATE <= frontmatter.published_at <= today:
        raise AiNotesContentError()
    if (
        frontmatter.updated_at is not None
        and frontmatter.updated_at < frontmatter.published_at
    ):
        raise AiNotesContentError()
