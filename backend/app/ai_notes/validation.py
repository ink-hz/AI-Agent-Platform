from __future__ import annotations

from datetime import date
import html
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import yaml

from .models import AiNotesIndex
from .repository import (
    AiNotesContentError,
    AiNotesRepository,
    iter_validated_articles,
)


_ALLOWED_LINK_SCHEMES = frozenset({"", "http", "https", "mailto"})
_INLINE_LINK = re.compile(
    r"!?\[[^\]]*\]\(\s*(?:<(?P<angle>[^>]+)>|(?P<plain>[^\s)]+))",
    re.IGNORECASE,
)
_REFERENCE_LINK = re.compile(
    r"^[ \t]{0,3}\[[^\]]+\]:[ \t]*(?:<(?P<angle>[^>]+)>|(?P<plain>\S+))",
    re.MULTILINE,
)
_AUTOLINK = re.compile(r"<(?P<destination>[^<>\s]+:[^<>]*)>")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]+")


def validate_publication(
    root: Path, marker_file: Path, *, today: date
) -> AiNotesIndex:
    try:
        repository = AiNotesRepository.load(root, today=today)
        markers = _load_markers(marker_file)
        for _, frontmatter, markdown in iter_validated_articles(root, today=today):
            if frontmatter.draft:
                continue
            searchable = json.dumps(
                frontmatter.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                sort_keys=True,
            )
            searchable = f"{searchable}\n{markdown}".casefold()
            if any(marker.casefold() in searchable for marker in markers):
                raise AiNotesContentError()
            if any(
                _normalized_scheme(destination) not in _ALLOWED_LINK_SCHEMES
                for destination in _link_destinations(markdown)
            ):
                raise AiNotesContentError()
        return repository.index()
    except AiNotesContentError:
        raise
    except Exception:
        raise AiNotesContentError() from None


def _load_markers(marker_file: Path) -> tuple[str, ...]:
    if marker_file.is_symlink() or not marker_file.is_file():
        raise AiNotesContentError()
    selected = yaml.safe_load(marker_file.read_text(encoding="utf-8"))
    if not isinstance(selected, dict) or set(selected) != {"markers"}:
        raise AiNotesContentError()
    markers = selected["markers"]
    if not isinstance(markers, list):
        raise AiNotesContentError()
    normalized: list[str] = []
    for marker in markers:
        if not isinstance(marker, str) or not marker.strip():
            raise AiNotesContentError()
        normalized.append(marker.strip())
    if len({marker.casefold() for marker in normalized}) != len(normalized):
        raise AiNotesContentError()
    return tuple(normalized)


def _link_destinations(markdown: str) -> tuple[str, ...]:
    destinations: list[str] = []
    for pattern in (_INLINE_LINK, _REFERENCE_LINK):
        for matched in pattern.finditer(markdown):
            destinations.append(matched.group("angle") or matched.group("plain"))
    destinations.extend(
        matched.group("destination") for matched in _AUTOLINK.finditer(markdown)
    )
    return tuple(destinations)


def _normalized_scheme(destination: str) -> str:
    decoded = html.unescape(unquote(destination.strip()))
    normalized = _CONTROL_OR_SPACE.sub("", decoded)
    return urlsplit(normalized).scheme.casefold()
