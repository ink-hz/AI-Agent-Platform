from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .models import ArticleFrontmatter


EXPECTED_AUTHOR = "苍渊"
EXPECTED_MOTTO = "博观而约取，厚积而薄发。"
ArticleSource = tuple[Path, ArticleFrontmatter, str]

_MARKDOWN = MarkdownIt("commonmark", {"html": True, "linkify": False})
_ACC_TITLE = re.compile(r"(?m)^\s*accTitle:\s*(?P<value>\S(?:.*\S)?)\s*$")
_ACC_DESCR = re.compile(r"(?m)^\s*accDescr:\s*(?P<value>\S(?:.*\S)?)\s*$")
_STYLING = re.compile(r"(?m)^\s*(?:classDef|style)\s+")
_SUBGRAPH = re.compile(r"(?mi)^\s*subgraph\s+(?P<id>[A-Za-z_][A-Za-z0-9_]*)\b")
_STYLE_FILL = re.compile(
    r"(?mi)^\s*style\s+(?P<targets>[A-Za-z0-9_,]+)\s+fill:\s*"
    r"(?P<fill>#[0-9A-F]{6})\b"
)


class AiNotesPublicationPolicyError(ValueError):
    pass


def _all_tokens(tokens: Iterable[Token]) -> Iterator[Token]:
    for token in tokens:
        yield token
        yield from token.children or ()


def _mermaid_blocks(tokens: Iterable[Token]) -> tuple[str, ...]:
    return tuple(
        token.content.rstrip("\n")
        for token in tokens
        if token.type == "fence" and token.info.strip() == "mermaid"
    )


def _metadata(pattern: re.Pattern[str], diagram: str) -> str:
    values = [matched.group("value") for matched in pattern.finditer(diagram)]
    if len(values) != 1:
        raise AiNotesPublicationPolicyError()
    return values[0]


def _validate_diagram(diagram: str) -> tuple[str, str]:
    if _STYLING.search(diagram) is None:
        raise AiNotesPublicationPolicyError()
    subgraphs = {matched.group("id") for matched in _SUBGRAPH.finditer(diagram)}
    for matched in _STYLE_FILL.finditer(diagram):
        targets = set(matched.group("targets").split(","))
        if targets & subgraphs and matched.group("fill").casefold() == "#f8fafc":
            raise AiNotesPublicationPolicyError()
    return _metadata(_ACC_TITLE, diagram), _metadata(_ACC_DESCR, diagram)


def validate_published_article_policy(entries: Iterable[ArticleSource]) -> None:
    titles: set[str] = set()
    descriptions: set[str] = set()
    for _, frontmatter, markdown in entries:
        if frontmatter.draft:
            continue
        if frontmatter.author != EXPECTED_AUTHOR or frontmatter.motto != EXPECTED_MOTTO:
            raise AiNotesPublicationPolicyError()
        top_level = _MARKDOWN.parse(markdown)
        flattened = tuple(_all_tokens(top_level))
        if any(
            token.type == "heading_open" and token.tag == "h1"
            for token in flattened
        ):
            raise AiNotesPublicationPolicyError()
        if any(token.type in {"html_block", "html_inline"} for token in flattened):
            raise AiNotesPublicationPolicyError()
        for diagram in _mermaid_blocks(top_level):
            title, description = _validate_diagram(diagram)
            if title in titles or description in descriptions:
                raise AiNotesPublicationPolicyError()
            titles.add(title)
            descriptions.add(description)
