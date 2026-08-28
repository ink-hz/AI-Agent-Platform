from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import shutil

import yaml

from app.ai_notes.models import ArticleFrontmatter
from app.ai_notes.repository import AiNotesRepository, parse_frontmatter
from app.ai_notes.validation import validate_publication


MODULE_ROOT = Path(__file__).resolve().parents[1] / "app" / "ai_notes"
CONTENT_ROOT = MODULE_ROOT / "content"
MARKER_FILE = MODULE_ROOT / "legacy_markers.yaml"
SOURCE_REVIEW = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "reviews"
    / "ai-notes-eight-article-source-review.md"
)
SOURCE_ROOT = Path("/Users/neo/Developer/personal/starship-blog-source/src/content/blog")
TODAY = date(2026, 8, 28)
FIRST_PUBLICATION_DATE = date(2026, 5, 25)
AUTHOR = "苍渊"
MOTTO = "博观而约取，厚积而薄发。"


BATCH_ARTICLES = (
    ("02-agent-architecture/02-agent-identity-access-control.md", "agent-identity-access-control"),
    ("04-ai-engineering/02-llm-inference-serving-engineering.md", "llm-inference-serving-engineering"),
    ("04-ai-engineering/03-ai-cloud-native-runtime.md", "ai-cloud-native-runtime"),
    ("04-ai-engineering/04-llm-agent-observability.md", "llm-agent-observability"),
    ("03-tools-and-frameworks/02-open-source-agent-runtime.md", "open-source-agent-runtime"),
    ("03-tools-and-frameworks/03-metabot-agent-control-bus.md", "metabot-agent-control-bus"),
    ("03-tools-and-frameworks/04-agent-framework-selection.md", "agent-framework-selection"),
    ("05-thinking-and-methods/02-intent-driven-ai-business-platform.md", "intent-driven-ai-business-platform"),
)

COMPLETED_BATCH_ARTICLES: tuple[str, ...] = ()

ARTICLE_CONTRACTS = {
    "agent-identity-access-control": {
        "category": "agent-architecture",
        "title": "Agent 身份与最小权限：代表谁、能做什么、如何审计",
        "tags": ("Agent", "身份与权限", "安全治理"),
    },
    "llm-inference-serving-engineering": {
        "category": "ai-engineering",
        "title": "LLM 推理服务工程：吞吐、延迟、缓存、路由与成本",
        "tags": ("LLM", "推理服务", "AI 工程"),
    },
    "ai-cloud-native-runtime": {
        "category": "ai-engineering",
        "title": "AI × 云原生运行时：调度、弹性、发布与故障恢复",
        "tags": ("AI 基础设施", "云原生", "运行时"),
    },
    "llm-agent-observability": {
        "category": "ai-engineering",
        "title": "LLM / Agent 可观测性：从调用链到质量闭环",
        "tags": ("LLM", "Agent", "可观测性"),
    },
    "open-source-agent-runtime": {
        "category": "tools-and-frameworks",
        "title": "Hermes 与 OpenClaw：开源 Agent 运行时的设计边界",
        "tags": ("Agent", "开源运行时", "架构分析"),
    },
    "metabot-agent-control-bus": {
        "category": "tools-and-frameworks",
        "title": "MetaBot 架构：Agent 的多渠道远程控制总线",
        "tags": ("Agent", "MetaBot", "远程控制"),
    },
    "agent-framework-selection": {
        "category": "tools-and-frameworks",
        "title": "主流 Agent 框架选型：从开发工具到生产运行时",
        "tags": ("Agent", "框架选型", "工程决策"),
    },
    "intent-driven-ai-business-platform": {
        "category": "thinking-and-methods",
        "title": "意图驱动的 AI 业务平台：从固定旅程到受控执行",
        "tags": ("AI Native", "业务平台", "意图驱动"),
    },
}

SOURCE_MANIFEST = (
    ("身份认证与访问控制-深度理论知识.md", 2625, "9df1b094bf6064974314e93985e65332803c500061a495f8fffc9f0408662ea5", "agent-identity-access-control"),
    ("身份认证与访问控制-理论架构设计.md", 2140, "c693c2e5ca30a47019191e001f0a8708beaed585dc03e7cdf67413e253efac01", "agent-identity-access-control"),
    ("AI-LLM系统架构深度指南.md", 2483, "f21f1316a7c66c5a5c920efe8c0f352dc2532af15d82265d35b58dfbab7dc784", "llm-inference-serving-engineering；辅助 llm-agent-observability"),
    ("AI-LLM系统架构理论指南.md", 1550, "c897f77ba9511c48358164c2c50b8f144703c983c0aa1dcedde9b20a6b145d8f", "llm-inference-serving-engineering；辅助 llm-agent-observability"),
    ("ai-cloud-native-opportunity.md", 124, "9c04bf78390c08bbe3c89e685bf4f407434c6f96c0ec63828662db75239c0a07", "ai-cloud-native-runtime"),
    ("Kubernetes与容器编排深度指南.md", 250, "4908263dc8ebdbe69106f50b8aa8f2b5030dd0a7e9fc5827945784e02dd31df4", "辅助 ai-cloud-native-runtime"),
    ("Kubernetes与容器编排理论指南.md", 1705, "12361a569b863b0de58f43366420c822f5b8568b42f243c38aab16aa0eef53ff", "辅助 ai-cloud-native-runtime"),
    ("可观测性与监控-深度理论知识.md", 1857, "546ea435a32227f0ec75e73e946e45e4cb5a6e092ee55deaf7d456de3c43b8ed", "llm-agent-observability"),
    ("Hermes-Agent架构分析与思考.md", 390, "aac8a4c575a11ae1a129c3e03d49e6217d2c02a92363b0689d5c5306c70227ad", "open-source-agent-runtime"),
    ("Clawdbot架构理论指南.md", 284, "838ae6a89bfeca305bb70bc016f975d7b12f34abef9fe73dd4638c22dedb6961", "open-source-agent-runtime"),
    ("MetaBot架构设计理论分析.md", 515, "f526d770501328c0aa12bb926ae379c640bebb3cd9540fe4b569a581caebded5", "metabot-agent-control-bus"),
    ("主流Agent框架深度分析-从架构本质到生产可用性.md", 323, "4edd175b19b9ac82be0ac9a92ed10d69ddfe14cac7611fa7b3df9f0a5866054b", "agent-framework-selection"),
    ("干掉用户旅程-意图驱动的业务平台架构设计.md", 379, "3a165ec7de6b712d9cbbc999ee6d7752b9954691f904f41a80d289bc0585d52b", "intent-driven-ai-business-platform"),
)


def batch_article_path(slug: str, *, root: Path = CONTENT_ROOT) -> Path:
    for relative_path, article_slug in BATCH_ARTICLES:
        if article_slug == slug:
            return root / relative_path
    raise AssertionError(f"unknown batch article: {slug}")


def assert_completed_batch_drafts(*, root: Path = CONTENT_ROOT) -> None:
    repository = AiNotesRepository.load(root, today=TODAY)
    assert repository.index().categories

    for slug in COMPLETED_BATCH_ARTICLES:
        path = batch_article_path(slug, root=root)
        frontmatter, markdown = parse_frontmatter(path)
        article = ArticleFrontmatter.model_validate(frontmatter)
        contract = ARTICLE_CONTRACTS[slug]
        category_frontmatter, _ = parse_frontmatter(path.parent / "_index.md")

        assert article.draft is True
        assert article.author == AUTHOR
        assert article.motto == MOTTO
        assert article.title == contract["title"]
        assert article.slug == slug
        assert category_frontmatter["slug"] == contract["category"]
        assert article.tags == contract["tags"]
        assert FIRST_PUBLICATION_DATE <= article.published_at <= TODAY
        assert article.updated_at is None or article.updated_at >= article.published_at
        assert markdown.strip()


def validate_completed_batch_as_publication_candidates(tmp_path: Path) -> None:
    candidate_root = tmp_path / "content"
    shutil.copytree(CONTENT_ROOT, candidate_root)

    for slug in COMPLETED_BATCH_ARTICLES:
        path = batch_article_path(slug, root=candidate_root)
        frontmatter, markdown = parse_frontmatter(path)
        frontmatter["draft"] = False
        serialized_frontmatter = yaml.safe_dump(
            frontmatter, allow_unicode=True, sort_keys=False
        ).strip()
        path.write_text(
            f"---\n{serialized_frontmatter}\n---\n\n{markdown}", encoding="utf-8"
        )

    validate_publication(candidate_root, MARKER_FILE, today=TODAY)


def test_source_review_records_the_exact_source_manifest() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    ledger_rows = tuple(
        line
        for line in review.splitlines()
        if line.startswith(f"| `{SOURCE_ROOT}/")
    )
    expected_rows = []

    for filename, line_count, digest, target in SOURCE_MANIFEST:
        path = SOURCE_ROOT / filename
        assert path.is_file()
        source = path.read_bytes()
        assert source.count(b"\n") == line_count
        assert hashlib.sha256(source).hexdigest() == digest
        expected_rows.append(
            f"| `{path}` | {line_count} | `{digest}` | {target} | "
            "未开始 | 未开始 | 未开始 | 未开始 | 未开始 |"
        )

    assert ledger_rows == tuple(expected_rows)
