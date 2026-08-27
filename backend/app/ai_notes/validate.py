from __future__ import annotations

from datetime import date
from pathlib import Path

from .repository import AiNotesContentError
from .validation import validate_publication


_MODULE_ROOT = Path(__file__).resolve().parent
_CONTENT_ROOT = _MODULE_ROOT / "content"
_MARKER_FILE = _MODULE_ROOT / "legacy_markers.yaml"


def main() -> int:
    try:
        index = validate_publication(
            _CONTENT_ROOT,
            _MARKER_FILE,
            today=date.today(),
        )
    except AiNotesContentError:
        print("AI notes content validation failed")
        return 1
    article_count = sum(len(category.articles) for category in index.categories)
    print(
        f"AI notes content valid: {len(index.categories)} categories, "
        f"{article_count} published articles"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
