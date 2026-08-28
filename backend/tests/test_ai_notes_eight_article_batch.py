from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import re
import shutil

import yaml

from app.ai_notes.models import AiNotesIndex, ArticleFrontmatter
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

COMPLETED_BATCH_ARTICLES: tuple[str, ...] = (
    "agent-identity-access-control",
    "llm-inference-serving-engineering",
)

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

IDENTITY_SOURCE_REVIEW_STATUS = {
    "身份认证与访问控制-深度理论知识.md": (
        "已精读：1-2625",
        "第1-5章：主体、令牌、授权、用户委托",
        "第3、6-7章：网关样例、旧日期、SSO与厂商清单",
        "已核验：RFC 8693、NIST、SPIFFE、OWASP（2026-08-28）",
        "全景与信任层留在主文章；本篇深化委托、授权、审计",
    ),
    "身份认证与访问控制-理论架构设计.md": (
        "已精读：1-2140",
        "第1、4-5、7.3章：身份、权限交集、最小权限、零信任",
        "第2-3、6、7.1-7.2及7.4-7.5章：登录网关、旧组织案例、产品横评",
        "已核验：RFC 8693、NIST、SPIFFE、OWASP（2026-08-28）",
        "状态机与信任分级留在主文章；本篇深化凭证与证据",
    ),
}

IDENTITY_PRIMARY_SOURCES = {
    "https://www.rfc-editor.org/rfc/rfc8693": "Token Exchange 区分主体与行动者并表达委托链",
    "https://csrc.nist.gov/pubs/sp/800/207/final": "零信任不因网络位置或资产归属授予隐式信任",
    "https://spiffe.io/docs/latest/spiffe-about/overview/": "工作负载可取得短时密码学身份文档并相互认证",
    "https://genai.owasp.org/llmrisk/llm062025-excessive-agency/": "过度功能、权限与自主性需要最小化能力和人工审批",
}

INFERENCE_SOURCE_REVIEW_STATUS = {
    "AI-LLM系统架构深度指南.md": (
        "已精读：1-2483",
        "第2、4章：KV Cache、连续批处理、投机解码、路由问题框架",
        "第1、3、4.3章及总结：Agent、RAG、MCP、旧 API 样例与固定性能数字",
        "已核验：PagedAttention、vLLM、TensorRT-LLM、SGLang（2026-08-28）",
        "应用请求链与 RAG 留在既有文章；本篇只写模型服务内部数据路径",
    ),
    "AI-LLM系统架构理论指南.md": (
        "已精读：1-1550",
        "第2、4.1-4.2章：Prefill/Decode、分页缓存、批调度、容量与路由取舍",
        "第1、3、4.3章及总结：Agent、RAG、MCP、厂商层级、固定价格与倍率",
        "已核验：PagedAttention、vLLM、TensorRT-LLM、SGLang（2026-08-28）",
        "不重述 Prompt/工具/输出验证与检索链；重写排队、缓存、路由与单位成本",
    ),
}

INFERENCE_PRIMARY_SOURCES = {
    "https://arxiv.org/abs/2309.06180": (
        "分页块让动态增长的 KV Cache 按需分配并支持请求内与请求间共享"
    ),
    "https://docs.vllm.ai/": (
        "前缀缓存只跳过共享前缀的 Prefill 计算，不缩短新 token 的 Decode"
    ),
    "https://nvidia.github.io/TensorRT-LLM/architecture/overview.html": (
        "调度器逐步选择活动请求，KV Cache 管理器负责分配、释放与维护缓存"
    ),
    "https://docs.sglang.ai/": (
        "Prefill 偏计算密集，Decode 偏内存密集；路由可连接分离的两类实例"
    ),
}


def batch_article_path(slug: str, *, root: Path = CONTENT_ROOT) -> Path:
    for relative_path, article_slug in BATCH_ARTICLES:
        if article_slug == slug:
            return root / relative_path
    raise AssertionError(f"unknown batch article: {slug}")


def assert_completed_batch_drafts(
    *, root: Path = CONTENT_ROOT
) -> tuple[ArticleFrontmatter, ...]:
    repository = AiNotesRepository.load(root, today=TODAY)
    assert repository.index().categories
    completed_articles = []

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
        completed_articles.append(article)

    return tuple(completed_articles)


def validate_completed_batch_as_publication_candidates(tmp_path: Path) -> AiNotesIndex:
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

    return validate_publication(candidate_root, MARKER_FILE, today=TODAY)


def test_completed_batch_helpers_enforce_drafts_and_validate_candidates(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    candidate_index = validate_completed_batch_as_publication_candidates(tmp_path)

    assert tuple(article.slug for article in completed_articles) == (
        COMPLETED_BATCH_ARTICLES
    )
    assert len(candidate_index.categories) == 5
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)


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
        status = {
            **IDENTITY_SOURCE_REVIEW_STATUS,
            **INFERENCE_SOURCE_REVIEW_STATUS,
        }.get(filename, ("未开始",) * 5)
        expected_rows.append(
            f"| `{path}` | {line_count} | `{digest}` | {target} | "
            f"{' | '.join(status)} |"
        )

    assert ledger_rows == tuple(expected_rows)


def test_identity_source_review_records_primary_source_verification() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")

    assert "访问日期：2026-08-28" in review
    for url, supported_claim in IDENTITY_PRIMARY_SOURCES.items():
        assert url in review
        assert supported_claim in review


def test_llm_serving_source_review_records_primary_source_verification() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    section_header = "## llm-inference-serving-engineering 精读结论"

    assert section_header in review
    section = review.split(section_header, 1)[1]
    assert "访问日期：2026-08-28" in section
    for url, supported_claim in INFERENCE_PRIMARY_SOURCES.items():
        assert url in section
        assert supported_claim in section


def test_agent_identity_access_control_draft_meets_contract(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        "agent-identity-access-control",
        "llm-inference-serving-engineering",
    )

    path = batch_article_path("agent-identity-access-control")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == (
        "Agent 身份与最小权限：代表谁、能做什么、如何审计"
    )
    assert frontmatter["slug"] == "agent-identity-access-control"
    assert tuple(frontmatter["tags"]) == (
        "Agent",
        "身份与权限",
        "安全治理",
    )
    assert frontmatter["publishedAt"] == TODAY
    assert frontmatter["updatedAt"] == TODAY
    assert markdown.lstrip().startswith("## ")
    assert AUTHOR not in markdown
    assert MOTTO not in markdown

    diagrams = tuple(
        re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown)
    )
    assert len(diagrams) == 3
    assert tuple(
        re.search(r"(?m)^\s*accTitle:\s*(.+?)\s*$", diagram).group(1)
        for diagram in diagrams
    ) == (
        "Agent 身份与委托链",
        "Agent 行动授权决策",
        "高风险操作审批闭环",
    )
    assert all(
        re.search(r"(?m)^\s*classDef\s+", diagram)
        for diagram in diagrams
    )
    candidate_index = validate_completed_batch_as_publication_candidates(
        tmp_path
    )
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 8
    assert all(
        len(
            set(
                re.findall(
                    r"(?m)\b([A-Z][A-Z0-9_]*)\s*(?=[\[\{\(])",
                    diagram,
                )
            )
        )
        <= 10
        for diagram in diagrams
    )

    for required_topic in (
        "用户委托",
        "工作负载身份",
        "Token Exchange",
        "最小权限",
        "审批绑定",
        "审计证据",
    ):
        assert required_topic in markdown
    assert re.search(
        r"\]\((?:\./)?enterprise-agent-system-architecture\)",
        markdown,
    )

    prohibited_product_or_feature_markers = (
        "SSO",
        "Okta",
        "Auth0",
        "Keycloak",
        "AWS IAM",
        "Azure AD",
        "Google Cloud IAM",
        "阿里云RAM",
        "腾讯云CAM",
        "华为云IAM",
        "玉符",
        "身份宝",
        "Casdoor",
    )
    legacy_markers = tuple(
        yaml.safe_load(MARKER_FILE.read_text(encoding="utf-8"))["markers"]
    )
    for prohibited in prohibited_product_or_feature_markers + legacy_markers:
        assert prohibited.casefold() not in markdown.casefold()


def test_agent_identity_article_preserves_rfc_8693_client_auth_boundary() -> None:
    path = batch_article_path("agent-identity-access-control")
    _, markdown = parse_frontmatter(path)

    assert (
        "RFC 8693 要求授权服务器验证 `subject_token`，并在请求提供 "
        "`actor_token` 时验证它。"
    ) in markdown
    assert (
        "是否接受未认证客户端由授权服务器的部署策略决定。"
    ) in markdown
    assert (
        "本文建议把 Agent 工作负载作为已认证的 OAuth client"
    ) in markdown
    assert "授权服务器仍需验证客户端或工作负载" not in markdown


def test_llm_inference_serving_engineering_draft_meets_contract(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        "agent-identity-access-control",
        "llm-inference-serving-engineering",
    )

    path = batch_article_path("llm-inference-serving-engineering")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == (
        "LLM 推理服务工程：吞吐、延迟、缓存、路由与成本"
    )
    assert frontmatter["slug"] == "llm-inference-serving-engineering"
    assert tuple(frontmatter["tags"]) == (
        "LLM",
        "推理服务",
        "AI 工程",
    )
    assert frontmatter["draft"] is True
    assert frontmatter["author"] == AUTHOR
    assert frontmatter["motto"] == MOTTO
    assert frontmatter["publishedAt"] == TODAY
    assert frontmatter["updatedAt"] == TODAY
    assert markdown.lstrip().startswith("## ")
    assert AUTHOR not in markdown
    assert MOTTO not in markdown

    diagrams = tuple(
        re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown)
    )
    assert len(diagrams) == 3
    assert tuple(
        re.search(r"(?m)^\s*accTitle:\s*(.+?)\s*$", diagram).group(1)
        for diagram in diagrams
    ) == (
        "LLM 推理请求数据路径",
        "连续批处理与 KV Cache 生命周期",
        "推理路由与容量决策",
    )
    descriptions = tuple(
        re.search(r"(?m)^\s*accDescr:\s*(.+?)\s*$", diagram).group(1)
        for diagram in diagrams
    )
    assert all(descriptions)
    assert len(set(descriptions)) == 3
    assert all(re.search(r"(?m)^\s*classDef\s+", diagram) for diagram in diagrams)
    assert all(
        re.search(
            r"(?m)^\s*classDef\s+\w+\s+fill:#(?:DBEAFE|EDE9FE|"
            r"CCFBF1|FEF3C7|DCFCE7|D1FAE5|FEE2E2|F3F4F6)",
            diagram,
        )
        for diagram in diagrams
    )
    assert all(
        len(
            set(
                re.findall(
                    r"(?m)\b([A-Z][A-Z0-9_]*)\s*(?=[\[\{\(])",
                    diagram,
                )
            )
        )
        <= 12
        for diagram in diagrams
    )

    for required_topic in (
        "Prefill",
        "Decode",
        "连续批处理",
        "KV Cache",
        "量化",
        "投机解码",
        "路由",
        "排队",
        "容量",
        "单位成本",
    ):
        assert required_topic in markdown
    assert re.search(
        r"\]\(\.\./foundations/llm-application-system-architecture\)",
        markdown,
    )
    assert not re.search(
        r"(?mi)^#{2,}\s+.*(?:RAG|工具调用|Prompt (?:编排|组装)|输出验证)",
        markdown,
    )
    for boundary_marker in ("RAG", "工具调用", "Prompt 组装", "输出验证"):
        assert markdown.casefold().count(boundary_marker.casefold()) <= 1

    candidate_index = validate_completed_batch_as_publication_candidates(
        tmp_path
    )
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 8


def test_llm_inference_metrics_cost_and_typography_contract() -> None:
    path = batch_article_path("llm-inference-serving-engineering")
    _, markdown = parse_frontmatter(path)

    assert (
        "| TPOT（Time per Output Token） | `(端到端延迟 - TTFT) / "
        "(输出 token 数 - 1)`，即首 token 之后的 Decode 时间对后续输出 token 的"
        "摊销值 |"
    ) in markdown
    assert (
        "| ITL（Inter-Token Latency） | 流式相邻输出事件之间的单次间隔 |"
    ) in markdown
    assert "TPOT / ITL" not in markdown
    assert "TPOT/ITL" not in markdown
    assert "投机解码单步可能返回多个 token" in markdown
    assert (
        "TPOT 按 `(端到端延迟 - TTFT) / (输出 token 数 - 1)` 计算"
    ) in markdown
    assert (
        "等价于首 token 之后的 Decode 时间除以首 token 之后的输出 token 数"
    ) in markdown
    assert "只有一个输出 token 时没有 TPOT 样本" in markdown
    assert "ITL 则逐次测量相邻流式输出事件的间隔" in markdown
    assert "单请求 Decode 总耗时除以输出 token 数" not in markdown
    assert "TPOT 把单请求 Decode 总耗时摊销到输出 token" not in markdown

    assert (
        "每个 SLO 内有效输出 token 成本\n"
        "= 推理服务总实付成本 / SLO 内有效输出 token 数"
    ) in markdown
    assert "闲置、失败重算和缓存传输通过这些实际账单进入分子" in markdown
    assert "各桶必须互斥" in markdown
    assert "(加速器租用 + 主机与传输 + 闲置容量 + 失败重算)" not in markdown
    assert "希缺资源" not in markdown
