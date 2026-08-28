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
PUBLISHED_ON = date(2026, 8, 27)
TODAY = date(2026, 8, 28)


def published_article(category_slug: str, article_slug: str) -> AiNoteArticle:
    repository = AiNotesRepository.load(CONTENT_ROOT, today=TODAY)
    article = repository.article(category_slug, article_slug)
    assert article is not None
    return article


def assert_clean_body(markdown: str) -> None:
    assert re.search(r"(?m)^#\s+", markdown) is None
    tokens = MarkdownIt("commonmark", {"html": True}).parse(markdown)
    assert not {token.type for token in tokens} & {"html_block", "html_inline"}


def mermaid_blocks(markdown: str) -> tuple[str, ...]:
    return tuple(re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown))


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
    assert article.published_at == PUBLISHED_ON
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("Claude Code", "Agent", "AI 开发工具")
    assert_clean_body(article.markdown)


def test_claude_code_note_visualizes_public_capabilities() -> None:
    article = published_article("tools-and-frameworks", "claude-code-architecture")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 3
    for label in ("公开入口", "上下文", "权限与策略", "内置工具", "MCP", "Hooks", "验证"):
        assert label in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY


def test_publishes_clean_ai_native_architecture_design_note() -> None:
    article = published_article("thinking-and-methods", "ai-native-architecture-design")
    assert article.title == "AI Native 辅助架构设计：协作方法与质量控制"
    assert article.filename == "ai-native-architecture-design.md"
    assert article.published_at == PUBLISHED_ON
    assert article.updated_at == PUBLISHED_ON
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("AI Native", "架构设计", "工程方法")
    assert_clean_body(article.markdown)


def test_publishes_clean_enterprise_agent_architecture_note() -> None:
    article = published_article("agent-architecture", "enterprise-agent-system-architecture")
    assert article.title == "企业级 Agent 系统架构：从循环引擎到信任层级"
    assert article.filename == "enterprise-agent-system-architecture.md"
    assert article.published_at == PUBLISHED_ON
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("Agent", "系统架构", "AI 工程")
    assert_clean_body(article.markdown)


def test_enterprise_agent_note_visualizes_runtime_and_trust() -> None:
    article = published_article("agent-architecture", "enterprise-agent-system-architecture")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 5
    assert "运行循环" in combined
    assert "信任决策" in combined
    assert "WaitingApproval" in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY


def test_publishes_clean_rag_retrieval_engineering_note() -> None:
    article = published_article("ai-engineering", "rag-retrieval-engineering")
    assert article.title == "RAG 检索工程：从向量索引到可验证回答"
    assert article.filename == "rag-retrieval-engineering.md"
    assert article.published_at == PUBLISHED_ON
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("RAG", "向量检索", "AI 工程")
    assert_clean_body(article.markdown)


def test_rag_note_visualizes_index_and_query_pipelines() -> None:
    article = published_article("ai-engineering", "rag-retrieval-engineering")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 4
    for label in ("索引链路", "查询链路", "HNSW", "BM25", "引用校验"):
        assert label in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY


def test_publishes_clean_agent_engineering_learning_map() -> None:
    article = published_article("foundations", "agent-engineering-learning-map")
    assert article.title == "Agent 工程学习地图：从模型循环到生产系统"
    assert article.filename == "agent-engineering-learning-map.md"
    assert article.published_at == PUBLISHED_ON
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("Agent", "学习地图", "AI 工程")
    assert_clean_body(article.markdown)


def test_learning_map_visualizes_the_system_and_progression() -> None:
    article = published_article("foundations", "agent-engineering-learning-map")
    diagrams = mermaid_blocks(article.markdown)
    assert len(diagrams) == 2
    combined = "\n".join(diagrams)
    assert "最小行动循环" in combined
    assert "能力递进" in combined
    assert "classDef" in combined
    assert article.published_at == PUBLISHED_ON
    assert article.updated_at == TODAY


def test_first_batch_is_exactly_the_approved_five_articles() -> None:
    index = validate_publication(CONTENT_ROOT, MARKER_FILE, today=TODAY)
    actual = {
        category.slug: tuple(article.slug for article in category.articles)
        for category in index.categories
    }
    assert actual == {
        "foundations": ("agent-engineering-learning-map",),
        "agent-architecture": ("enterprise-agent-system-architecture",),
        "tools-and-frameworks": ("claude-code-architecture",),
        "ai-engineering": ("rag-retrieval-engineering",),
        "thinking-and-methods": ("ai-native-architecture-design",),
    }
