from __future__ import annotations

from datetime import date
from pathlib import Path
import re

from markdown_it import MarkdownIt
import yaml

from app.ai_notes.repository import AiNoteArticle, AiNotesRepository
from app.ai_notes.validation import validate_publication


MODULE_ROOT = Path(__file__).resolve().parents[1] / "app" / "ai_notes"
CONTENT_ROOT = MODULE_ROOT / "content"
MARKER_FILE = MODULE_ROOT / "legacy_markers.yaml"
TODAY = date(2026, 8, 27)


def published_article(category_slug: str, article_slug: str) -> AiNoteArticle:
    repository = AiNotesRepository.load(CONTENT_ROOT, today=TODAY)
    article = repository.article(category_slug, article_slug)
    assert article is not None
    return article


def assert_clean_body(markdown: str) -> None:
    assert re.search(r"(?m)^#\s+", markdown) is None
    tokens = MarkdownIt("commonmark", {"html": True}).parse(markdown)
    assert not {token.type for token in tokens} & {"html_block", "html_inline"}


def test_legacy_marker_file_covers_identified_source_branding() -> None:
    selected = yaml.safe_load(MARKER_FILE.read_text(encoding="utf-8"))
    assert selected == {
        "markers": ["inkbot.cn", "Ink Blog", "STARSHIP", "星舰"]
    }


def test_current_production_content_passes_publication_validation() -> None:
    index = validate_publication(CONTENT_ROOT, MARKER_FILE, today=TODAY)
    assert len(index.categories) == 5


def test_publishes_clean_claude_code_architecture_note() -> None:
    article = published_article("tools-and-frameworks", "claude-code-architecture")
    assert article.title == "Claude Code 架构分析：公开能力与工程启发"
    assert article.filename == "claude-code-architecture.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("Claude Code", "Agent", "AI 开发工具")
    assert_clean_body(article.markdown)


def test_publishes_clean_ai_native_architecture_design_note() -> None:
    article = published_article("thinking-and-methods", "ai-native-architecture-design")
    assert article.title == "AI Native 辅助架构设计：协作方法与质量控制"
    assert article.filename == "ai-native-architecture-design.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("AI Native", "架构设计", "工程方法")
    assert_clean_body(article.markdown)


def test_publishes_clean_enterprise_agent_architecture_note() -> None:
    article = published_article("agent-architecture", "enterprise-agent-system-architecture")
    assert article.title == "企业级 Agent 系统架构：从循环引擎到信任层级"
    assert article.filename == "enterprise-agent-system-architecture.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("Agent", "系统架构", "AI 工程")
    assert_clean_body(article.markdown)
