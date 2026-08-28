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
LLM_APPLICATION_PUBLISHED_ON = date(2026, 8, 28)
BATCH_PUBLISHED_ON = date(2026, 8, 28)
FIRST_EMPLOYMENT_DATE = date(2026, 5, 25)

BATCH_ARTICLES = (
    ("agent-architecture", "agent-identity-access-control"),
    ("ai-engineering", "llm-inference-serving-engineering"),
    ("ai-engineering", "ai-cloud-native-runtime"),
    ("ai-engineering", "llm-agent-observability"),
    ("tools-and-frameworks", "open-source-agent-runtime"),
    ("tools-and-frameworks", "metabot-agent-control-bus"),
    ("tools-and-frameworks", "agent-framework-selection"),
    ("thinking-and-methods", "intent-driven-ai-business-platform"),
)


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
    assert "class O infra;" in combined
    assert "class M model;" in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY


def test_publishes_clean_ai_native_architecture_design_note() -> None:
    article = published_article("thinking-and-methods", "ai-native-architecture-design")
    assert article.title == "AI Native 辅助架构设计：协作方法与质量控制"
    assert article.filename == "ai-native-architecture-design.md"
    assert article.published_at == PUBLISHED_ON
    assert article.updated_at == TODAY
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.tags == ("AI Native", "架构设计", "工程方法")
    assert_clean_body(article.markdown)


def test_ai_native_note_visualizes_human_ai_responsibility() -> None:
    article = published_article("thinking-and-methods", "ai-native-architecture-design")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 2
    for label in ("AI 辅助", "人负责", "目标与约束", "候选方案", "批准与责任"):
        assert label in combined
    assert [diagram.splitlines()[0] for diagram in diagrams] == [
        "flowchart LR",
        "flowchart LR",
    ]
    assert "class A1,A2,A3 model" in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY


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
    assert "class R infra;" in combined
    assert "class A infra;" in combined
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
    assert [diagram.splitlines()[0] for diagram in diagrams] == [
        "flowchart LR",
        "flowchart LR",
        "flowchart TD",
        "flowchart LR",
    ]
    assert "class P,C tool;" in combined
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


def test_mermaid_group_backgrounds_do_not_use_neutral_gray_panels() -> None:
    repository = AiNotesRepository.load(CONTENT_ROOT, today=TODAY)
    for category in repository.index().categories:
        for summary in category.articles:
            article = repository.article(category.slug, summary.slug)
            assert article is not None
            for diagram in mermaid_blocks(article.markdown):
                assert re.search(
                    r"(?mi)^\s*style\s+\S+\s+fill:#F8FAFC\b",
                    diagram,
                ) is None


def test_publishes_clean_llm_application_system_architecture_note() -> None:
    article = published_article("foundations", "llm-application-system-architecture")
    assert article.title == "LLM 应用系统架构：从一次请求到可靠回答"
    assert article.filename == "llm-application-system-architecture.md"
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.published_at == LLM_APPLICATION_PUBLISHED_ON
    assert article.updated_at == LLM_APPLICATION_PUBLISHED_ON
    assert article.tags == ("LLM", "系统架构", "AI 工程")
    assert_clean_body(article.markdown)


def test_llm_application_note_visualizes_reliable_answer_boundary() -> None:
    article = published_article("foundations", "llm-application-system-architecture")
    diagrams = mermaid_blocks(article.markdown)
    assert len(diagrams) == 3
    application_layers, reliable_answer, capability_choice = diagrams

    assert "应用系统分层" in application_layers
    assert "模型推理" in application_layers
    assert "检索" in application_layers
    assert "工具" in application_layers
    assert "class N,O,G infra;" in application_layers
    assert "class M,X model;" in application_layers
    assert "class I,V policy;" in application_layers

    assert "从请求到可靠回答" in reliable_answer
    assert "输出验证" in reliable_answer
    assert "完成证据" in reliable_answer
    assert "class P,Z success;" in reliable_answer
    assert "class X risk;" in reliable_answer
    assert "class C,F,V policy;" in reliable_answer

    assert "能力选择边界" in capability_choice
    assert "需要调用外部工具" in capability_choice
    assert "叠加 RAG 检索证据" in capability_choice
    assert "异步任务运行时包装" in capability_choice
    assert "class Q,I,W infra;" in capability_choice
    assert "class S,G model;" in capability_choice
    assert "class T,P,K,L policy;" in capability_choice
    assert "class F success;" in capability_choice

    assert all("flowchart LR" not in diagram for diagram in diagrams)
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)


def test_production_catalog_contains_exactly_fourteen_articles() -> None:
    index = validate_publication(CONTENT_ROOT, MARKER_FILE, today=TODAY)
    actual = {
        category.slug: tuple(article.slug for article in category.articles)
        for category in index.categories
    }
    assert actual == {
        "foundations": (
            "agent-engineering-learning-map",
            "llm-application-system-architecture",
        ),
        "agent-architecture": (
            "enterprise-agent-system-architecture",
            "agent-identity-access-control",
        ),
        "tools-and-frameworks": (
            "claude-code-architecture",
            "open-source-agent-runtime",
            "metabot-agent-control-bus",
            "agent-framework-selection",
        ),
        "ai-engineering": (
            "rag-retrieval-engineering",
            "llm-inference-serving-engineering",
            "ai-cloud-native-runtime",
            "llm-agent-observability",
        ),
        "thinking-and-methods": (
            "ai-native-architecture-design",
            "intent-driven-ai-business-platform",
        ),
    }


def test_eight_article_batch_is_published_together_on_the_execution_date() -> None:
    assert BATCH_PUBLISHED_ON >= FIRST_EMPLOYMENT_DATE
    articles = tuple(
        published_article(category_slug, article_slug)
        for category_slug, article_slug in BATCH_ARTICLES
    )

    assert len(articles) == 8
    assert all(article.published_at == BATCH_PUBLISHED_ON for article in articles)
    assert all(article.updated_at == BATCH_PUBLISHED_ON for article in articles)
