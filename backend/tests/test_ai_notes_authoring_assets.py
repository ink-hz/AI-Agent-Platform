from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STANDARD = ROOT / "docs" / "standards" / "ai-engineering-notes.md"
TEMPLATE = ROOT / "docs" / "templates" / "ai-engineering-note.md"


def test_authoring_standard_preserves_human_quality_gates() -> None:
    source = STANDARD.read_text(encoding="utf-8")
    for required in (
        "全文精读",
        "一手资料",
        "跨文章去重",
        "1440×900",
        "390×844",
        "Critical",
        "Important",
        "draft: true",
        "draft: false",
    ):
        assert required in source


def test_template_starts_as_a_white_canvas_draft_by_cangyuan() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    for required in (
        "author: 苍渊",
        "motto: 博观而约取，厚积而薄发。",
        "draft: true",
        "accTitle:",
        "accDescr:",
        "fill:#FFFFFF",
        "classDef",
    ):
        assert required in source
    assert "style SYSTEM fill:#F8FAFC" not in source
