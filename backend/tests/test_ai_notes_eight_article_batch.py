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
    "ai-cloud-native-runtime",
    "llm-agent-observability",
    "open-source-agent-runtime",
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

CLOUD_NATIVE_SOURCE_REVIEW_STATUS = {
    "ai-cloud-native-opportunity.md": (
        "已精读：1-124",
        "问题与原则：异构资源、弹性、发布、容错",
        "架构样例、厂商选型、固定倍率与 HPA 万能化表述",
        "已核验：Kubernetes、Kueue、KServe、Device Plugin（2026-08-28）",
        "推理算法留在既有篇；本篇只写运行时资源与运维闭环",
    ),
    "Kubernetes与容器编排深度指南.md": (
        "已精读：1-250（通用概念辅助）",
        "调度、隔离、声明式发布、故障恢复概念",
        "安装命令、对象清单、旧版本行为与厂商 GPU 功能",
        "已核验：Kubernetes、Kueue、KServe、Device Plugin（2026-08-28）",
        "不迁移 Kubernetes 百科；只辅助 AI 运行时边界",
    ),
    "Kubernetes与容器编排理论指南.md": (
        "已精读：1-1705（通用概念辅助）",
        "一致性、队列调度、隔离、渐进发布与恢复概念",
        "组件百科、Helm/GitOps 教程、厂商设备与固定性能数字",
        "已核验：Kubernetes、Kueue、KServe、Device Plugin（2026-08-28）",
        "不复述通用编排；重写为制品、调度和恢复链",
    ),
}

CLOUD_NATIVE_PRIMARY_SOURCES = {
    "https://kubernetes.io/docs/concepts/scheduling-eviction/": (
        "调度把 Pod 匹配到节点；抢占与驱逐分别处理优先级和中断"
    ),
    "https://kueue.sigs.k8s.io/docs/overview/": (
        "Kueue 管理配额消费并决定工作负载等待、准入或抢占"
    ),
    "https://kserve.github.io/website/": (
        "KServe 控制面覆盖模型生命周期、版本跟踪与灰度发布"
    ),
    "https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/": (
        "Device Plugin 向 kubelet 暴露 GPU 等设备资源供工作负载请求"
    ),
}

OBSERVABILITY_SOURCE_REVIEW_STATUS = {
    "可观测性与监控-深度理论知识.md": (
        "已精读：1-1857",
        "上下文传播、结构化 trace、采样、高基数、SLO 与信号关联",
        "通用三支柱教材、产品栈与配置、固定阈值、采样率和成本数字",
        "已核验：OpenTelemetry GenAI、W3C Trace Context、NIST（2026-08-28）",
        "RAG 算法与 Agent 状态机留在主文章；本篇只记录证据与质量信号",
    ),
}

OBSERVABILITY_PRIMARY_SOURCES = {
    "https://opentelemetry.io/docs/specs/semconv/gen-ai/": (
        "GenAI 约定已迁至独立官方仓库，当前整体状态为 Development"
    ),
    "https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/": (
        "原属性注册表中的 GenAI 字段已标为 Deprecated 并指向独立仓库"
    ),
    "https://www.w3.org/TR/trace-context/": (
        "traceparent 提供跨组件关联所需的 trace-id、parent-id 与 trace-flags"
    ),
    "https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence": (
        "持续监测、内容来源、结构化反馈和独立评估共同支撑生成式 AI 风险管理"
    ),
}

OPEN_SOURCE_RUNTIME_SOURCE_REVIEW_STATUS = {
    "Hermes-Agent架构分析与思考.md": (
        "已精读：1-390",
        "Agent loop、context/session、skills/tools、memory、sandbox 与 recovery 问题框架",
        "动态版本、数量、排行、营销结论与无法复核的生产效果",
        "已核验：Hermes 与 OpenClaw 官方仓库快照（2026-08-28）",
        "Claude Code 使用体验留在既有文章；本篇只比较运行时责任边界",
    ),
    "Clawdbot架构理论指南.md": (
        "已精读：1-284",
        "Gateway、channel routing、session、tools/skills、sandbox 与 recovery 问题框架",
        "旧项目名当现名、动态渠道数、成熟度结论与安全泛化承诺",
        "已核验：Hermes 与 OpenClaw 官方仓库快照（2026-08-28）",
        "企业 Agent 全景留在主文章；本篇抽象 provider/model/runtime/channel 和 ownership",
    ),
}

OPEN_SOURCE_RUNTIME_PRIMARY_SOURCES = {
    "https://github.com/NousResearch/hermes-agent": (
        "Hermes 公开仓库 main@35328345d5e3b5badc47271bdb8828e1fd2d25f4"
    ),
    "https://github.com/openclaw/openclaw": (
        "OpenClaw 公开仓库 main@468054f93c431bfe192327f439efe325be52f2b4"
    ),
    "https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent-runtimes.md": (
        "provider、model、agent runtime 与 channel 是四个不同责任层"
    ),
    "https://github.com/openclaw/openclaw/blob/main/docs/agent-runtime-architecture.md": (
        "OpenClaw 官方文档列出 built-in runtime 的代码布局与边界"
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


def mermaid_principal_node_ids(source: str) -> set[str]:
    subgraph_ids = set(
        re.findall(
            r"(?mi)^\s*subgraph\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
            source,
        )
    )
    node_ids = set(
        re.findall(
            r"(?m)\b([A-Za-z_][A-Za-z0-9_-]*)\s*(?=[\[\{\(])",
            source,
        )
    )
    edge_operator = r"(?:-->|---|==>|-\.[^\n]*?\.->)"
    node_ids.update(
        re.findall(
            rf"(?m)(?:^|[;\s])([A-Za-z_][A-Za-z0-9_-]*)\s*"
            rf"(?={edge_operator})",
            source,
        )
    )
    node_ids.update(
        re.findall(
            rf"(?m){edge_operator}\s*(?:\|[^|\n]*\|\s*)?"
            r"([A-Za-z_][A-Za-z0-9_-]*)\b",
            source,
        )
    )
    return node_ids - subgraph_ids


def cloud_native_contains_forbidden_tutorial_or_hpa(markdown: str) -> bool:
    prohibited_patterns = (
        r"(?im)^#{2,}\s+.*(?:install(?:ing|ation)?\s+kubernetes|"
        r"kubernetes\s+(?:install(?:ing|ation)?|安装)|安装\s*kubernetes)",
        r"(?im)^```\s*(?:ya?ml|bash|sh|shell)\b",
        r"(?i)(?<![\w])(?:kubectl|kubeadm)(?![\w])",
        r"(?i)\bhelm\s+install\b",
    )
    if any(re.search(pattern, markdown) for pattern in prohibited_patterns):
        return True

    hpa_pattern = (
        r"(?:\bHPA\b|Horizontal\s+Pod\s+Autoscal(?:er|ing)|"
        r"水平\s*Pod\s*自动扩缩容)"
    )
    exclusivity_or_sufficiency_patterns = (
        rf"(?:仅|只)(?:用|靠|依赖)?\s*{hpa_pattern}",
        rf"(?:单靠|单独依赖)\s*{hpa_pattern}",
        rf"{hpa_pattern}\s*(?:本身\s*)?(?:就\s*)?"
        r"(?:够(?:了)?|足够|足以|即可)",
        rf"{hpa_pattern}\s*(?:单独|独自)\s*(?:就\s*)?"
        r"(?:能|可以|足以|够)",
        rf"(?:only\s+{hpa_pattern}|(?:use|need)\s+only\s+{hpa_pattern})\b",
        rf"(?:use|rely\s+on)\s+{hpa_pattern}\s+only\b",
        rf"{hpa_pattern}\s+alone\s+(?:can|will|solves?|handles?|works?|"
        r"is\s+(?:sufficient|enough))\b",
        rf"{hpa_pattern}\s+only\s+works?\s+for\s+"
        r"(?:inference\s+)?elasticity\b",
        rf"{hpa_pattern}\s+(?:is\s+)?(?:enough|sufficient)\b",
    )
    negating_prefix = re.compile(
        r"(?i)(?:不要|不能|不可|不应|并非|不是|并不|无需|不必|"
        r"do\s+not|don't|cannot|can't|should\s+not|not)\s*$"
    )
    negating_suffix = re.compile(
        r"(?i)^\s*(?:(?:本身\s*)?(?:是|也|仍然)?\s*(?:并)?"
        r"(?:不够|不足|不能|不可|不应|无法)|"
        r"(?:is|are|was|were)\s+not\s+(?:enough|sufficient)|"
        r"(?:cannot|can't|does\s+not|doesn't)\b)"
    )
    clause_separator = re.compile(r"[。！？.!?；;，,\n]")
    for pattern in exclusivity_or_sufficiency_patterns:
        for claim in re.finditer(rf"(?i){pattern}", markdown):
            prefix = clause_separator.split(markdown[:claim.start()])[-1]
            if negating_prefix.search(prefix):
                continue
            suffix = clause_separator.split(markdown[claim.end():], maxsplit=1)[0]
            if negating_suffix.search(suffix):
                continue
            return True

    hpa_terms = tuple(re.finditer(rf"(?i){hpa_pattern}", markdown))
    absolute_terms = tuple(
        re.finditer(
            r"(?i)万能|所有|普遍|一律|唯一|必然|必须|都应|通用|"
            r"universal|\ball\b|\bevery\b",
            markdown,
        )
    )
    for hpa_term in hpa_terms:
        for absolute_term in absolute_terms:
            distance = max(
                hpa_term.start(), absolute_term.start()
            ) - min(hpa_term.end(), absolute_term.end())
            if distance > 48:
                continue
            between_start = min(hpa_term.end(), absolute_term.end())
            between_end = max(hpa_term.start(), absolute_term.start())
            between = markdown[between_start:between_end]
            if re.search(r"[。！？.!?；;\n]", between):
                continue
            if hpa_term.end() <= absolute_term.start():
                if re.fullmatch(
                    r"(?i)\s*(?:并非|不是|并不|isn't|"
                    r"is\s+not(?:\s+an?)?|not(?:\s+an?)?)\s*",
                    between,
                ):
                    continue
                if re.fullmatch(
                    r"\s*(?:并非|不是)\s*所有[^,，。！？；;\n]{0,24}\s*",
                    between,
                ):
                    continue
            else:
                prefix = markdown[max(0, absolute_term.start() - 16):absolute_term.start()]
                if re.search(
                    r"(?i)(?:并非|不是|并不|不应|不要|无需|不必|"
                    r"not|should\s+not|do\s+not)\s*$",
                    prefix,
                ):
                    continue
            return True
    return False


def observability_contains_fixed_genai_schema_claim(markdown: str) -> bool:
    subject = r"(?:`?gen_ai\.[a-z0-9_.*]+`?|OpenTelemetry\s+GenAI\s+(?:属性|字段|schema))"
    permanence = (
        r"(?:已经是|现已是|是|属于|provides?|is|are)?\s*(?:a\s+)?"
        r"(?:永久(?:的)?字段合同|固定(?:的)?\s*(?:schema|字段合同)|"
        r"稳定(?:的)?\s*(?:schema|字段合同|属性)|永不变化|"
        r"permanent\s+(?:schema|contract)|stable\s+(?:schema|contract|attributes?))"
    )
    negation = re.compile(
        r"(?i)(?:不是|并非|不能视为|不应视为|not|isn't|is\s+not)\s*$"
    )
    clause_separator = re.compile(r"[。！？.!?；;\n]")
    for claim in re.finditer(rf"(?i){subject}\s*{permanence}", markdown):
        prefix = clause_separator.split(markdown[:claim.start()])[-1]
        if negation.search(prefix):
            continue
        return True
    return False


def observability_contains_duplicate_rag_or_agent_tutorial(markdown: str) -> bool:
    if re.search(
        r"(?mi)^#{2,}\s+.*(?:向量检索|RAG\s*(?:算法|实现)|"
        r"Agent\s*(?:状态机|运行循环)|智能体\s*(?:状态机|运行循环))",
        markdown,
    ):
        return True

    rag_tutorial_markers = ("HNSW", "IVF", "BM25", "Embedding")
    agent_tutorial_markers = ("ReAct", "Plan-and-Execute", "WaitingApproval")
    return (
        sum(marker.casefold() in markdown.casefold() for marker in rag_tutorial_markers)
        >= 2
        or sum(
            marker.casefold() in markdown.casefold()
            for marker in agent_tutorial_markers
        )
        >= 2
    )


def source_review_h2_section(markdown: str, section_header: str) -> str:
    matched = re.search(
        rf"(?m)^{re.escape(section_header)}\s*$",
        markdown,
    )
    if matched is None:
        raise AssertionError(f"missing source review section: {section_header}")
    remainder = markdown[matched.end():]
    next_h2 = re.search(r"(?m)^##\s+", remainder)
    return remainder[:next_h2.start()] if next_h2 else remainder


def observability_has_effective_context_contract(markdown: str) -> bool:
    required_markers = (
        "effective_context:",
        "assembly_rule_version:",
        "task_state_snapshot_ref:",
        "prompt_messages_ref:",
        "retrieval_evidence_refs:",
        "tool_result_refs:",
        "input_token_budget:",
        "used_input_tokens:",
        "truncated:",
        "content_digest:",
    )
    normalized = markdown.casefold()
    return all(marker.casefold() in normalized for marker in required_markers)


def observability_has_call_level_usage_contract(markdown: str) -> bool:
    required_markers = (
        "model_calls:",
        "call_id:",
        "resolved_model:",
        "usage_source:",
        "billing_or_tokenizer_basis:",
        "retry_of:",
        "input_tokens:",
        "output_tokens:",
        "cache_read_tokens:",
        "cache_write_tokens:",
        "reasoning_tokens:",
        "execution_usage:",
        "source_call_ids:",
    )
    normalized = markdown.casefold()
    return all(marker.casefold() in normalized for marker in required_markers)


def open_source_runtime_uses_legacy_name_as_current(markdown: str) -> bool:
    allowed_boundary = (
        "源稿旧称 `Clawdbot` 仅用于项目更名核验；"
        "当前项目名是 OpenClaw。"
    )
    return "Clawdbot" in markdown.replace(allowed_boundary, "")


def open_source_runtime_contains_dynamic_ranking(markdown: str) -> bool:
    prohibited_patterns = (
        r"(?i)\bstar\s*(?:数|量|count)?\b|\bstars?\b",
        r"(?:功能|项目|框架)?(?:排行榜|排名第)",
        r"(?i)(?<![\w.])\d[\d,.]*[kKmM]?\+?\s*"
        r"(?:个|种|条|款)?\s*"
        r"(?:模型|工具|渠道|平台|执行环境|providers?|models?|tools?|channels?)\b",
        r"(?i)(?<![\w.])\d[\d,.]*[kKmM]?\+?\s*(?:commits?|PRs?)\b",
    )
    return any(re.search(pattern, markdown) for pattern in prohibited_patterns)


def open_source_runtime_contains_unmarked_inference(markdown: str) -> bool:
    for clause in re.split(r"[\n。！？；;]", markdown):
        if "推断" not in clause:
            continue
        if "从公开结构可以推断" in clause or "本文推断" in clause:
            continue
        return True
    return False


def open_source_runtime_confuses_layers(markdown: str) -> bool:
    prohibited_patterns = (
        r"(?i)\bprovider\b\s*(?:就是|等同于|=)\s*(?:\bmodel\b|模型本身)",
        r"(?i)\bmodel\b\s*(?:就是|等同于|=)\s*"
        r"(?:\bprovider\b|模型\s*/?\s*API\s*提供方)",
        r"(?i)(?:\bagent\s+)?\bruntime\b\s*(?:就是|等同于|=)\s*"
        r"(?:\bprovider\b|\bchannel\b|模型提供方|渠道)",
        r"(?i)\bchannel\b\s*(?:就是|等同于|=)\s*"
        r"(?:\bprovider\b|\bmodel\b|(?:\bagent\s+)?\bruntime\b|模型|运行时)",
    )
    return any(re.search(pattern, markdown) for pattern in prohibited_patterns)


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
            **CLOUD_NATIVE_SOURCE_REVIEW_STATUS,
            **OBSERVABILITY_SOURCE_REVIEW_STATUS,
            **OPEN_SOURCE_RUNTIME_SOURCE_REVIEW_STATUS,
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


def test_cloud_native_source_review_records_primary_source_verification() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    section_header = "## ai-cloud-native-runtime 精读结论"

    assert section_header in review
    section = review.split(section_header, 1)[1]
    assert "访问日期：2026-08-28" in section
    for url, supported_claim in CLOUD_NATIVE_PRIMARY_SOURCES.items():
        assert url in section
        assert supported_claim in section


def test_llm_agent_observability_source_review_records_primary_sources() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    section_header = "## llm-agent-observability 精读结论"

    assert section_header in review
    section = source_review_h2_section(review, section_header)
    assert "访问日期：2026-08-28" in section
    for url, supported_claim in OBSERVABILITY_PRIMARY_SOURCES.items():
        assert url in section
        assert supported_claim in section


def test_observability_source_review_section_stops_at_next_h2() -> None:
    section_header = "## llm-agent-observability 精读结论"
    fixture = (
        f"{section_header}\n\n只属于本节。\n\n"
        "## open-source-agent-runtime 精读结论\n\n"
        "https://www.w3.org/TR/trace-context/\n"
        "traceparent 提供跨组件关联所需的 trace-id、parent-id 与 trace-flags\n"
    )

    section = source_review_h2_section(fixture, section_header)

    assert "只属于本节" in section
    assert "open-source-agent-runtime" not in section
    assert "https://www.w3.org/TR/trace-context/" not in section


def test_open_source_runtime_source_review_records_repo_snapshots() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    section_header = "## open-source-agent-runtime 精读结论"

    assert section_header in review
    section = source_review_h2_section(review, section_header)
    assert "访问日期：2026-08-28" in section
    for url, supported_claim in OPEN_SOURCE_RUNTIME_PRIMARY_SOURCES.items():
        assert url in section
        assert supported_claim in section
    for evidence_path in (
        "README.md",
        "agent/conversation_loop.py",
        "model_tools.py",
        "tools/skills_tool.py",
        "tools/terminal_tool.py",
        "gateway/session_db_recovery.py",
        "docs/concepts/agent-runtimes.md",
        "docs/agent-runtime-architecture.md",
        "docs/concepts/agent-loop.md",
        "docs/concepts/session.md",
        "docs/concepts/memory.md",
        "docs/tools/skills.md",
        "docs/gateway/sandbox-vs-tool-policy-vs-elevated.md",
        "docs/gateway/restart-recovery.md",
        "docs/channels/channel-routing.md",
    ):
        assert evidence_path in section


def test_agent_identity_access_control_draft_meets_contract(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        COMPLETED_BATCH_ARTICLES
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
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)
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
        COMPLETED_BATCH_ARTICLES
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
        len(mermaid_principal_node_ids(diagram)) <= 12
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
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)


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


def test_ai_cloud_native_runtime_draft_meets_contract(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        COMPLETED_BATCH_ARTICLES
    )

    path = batch_article_path("ai-cloud-native-runtime")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == (
        "AI × 云原生运行时：调度、弹性、发布与故障恢复"
    )
    assert frontmatter["slug"] == "ai-cloud-native-runtime"
    assert tuple(frontmatter["tags"]) == (
        "AI 基础设施",
        "云原生",
        "运行时",
    )
    assert frontmatter["draft"] is True
    assert frontmatter["author"] == AUTHOR
    assert frontmatter["motto"] == MOTTO
    assert frontmatter["publishedAt"] == TODAY
    assert frontmatter["updatedAt"] == TODAY
    assert markdown.lstrip().startswith("## ")
    assert AUTHOR not in markdown
    assert MOTTO not in markdown

    diagrams = tuple(re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown))
    assert len(diagrams) == 3
    assert tuple(
        re.search(r"(?m)^\s*accTitle:\s*(.+?)\s*$", diagram).group(1)
        for diagram in diagrams
    ) == (
        "AI 云原生运行时分层",
        "模型制品到灰度发布链",
        "AI 工作负载故障恢复流程",
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
        len(mermaid_principal_node_ids(diagram)) <= 12
        for diagram in diagrams
    )

    for required_topic in (
        "CPU",
        "GPU",
        "Device Plugin",
        "调度",
        "排队",
        "模型制品",
        "节点缓存",
        "隔离",
        "弹性",
        "灰度",
        "回滚",
        "检查点",
        "故障恢复",
    ):
        assert required_topic in markdown
    assert (
        "Pending Pod 会进入 kube-scheduler 的调度队列，但这不等于平台已经有"
        "具备租户配额、公平和取消语义的工作负载级队列。"
    ) in markdown
    assert (
        "对 Kueue 管理的工作负载，准入决定何时可以开始创建 Pod，随后才由 "
        "kube-scheduler 完成 Pod 到节点的放置；未纳入 Kueue 管理的普通 Pod "
        "不经过这层准入。"
    ) in markdown
    assert re.search(
        r"\]\((?:\./)?llm-inference-serving-engineering\)",
        markdown,
    )
    for inference_detail in (
        "PagedAttention",
        "连续批处理",
        "投机解码",
        "KV Cache",
    ):
        assert markdown.casefold().count(inference_detail.casefold()) <= 1

    assert not cloud_native_contains_forbidden_tutorial_or_hpa(markdown)

    candidate_index = validate_completed_batch_as_publication_candidates(
        tmp_path
    )
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)


def test_cloud_native_contract_guards_reject_bypass_variants() -> None:
    bare_nodes = "flowchart LR\n" + "\n".join(
        f"N{index} --> N{index + 1}" for index in range(12)
    )
    assert len(mermaid_principal_node_ids(bare_nodes)) == 13

    for prohibited in (
        "## Installing Kubernetes",
        "```YAML\nkind: Pod\n```",
        "Run `kubectl` apply next.",
        "kubeadm init",
        "helm   install runtime chart",
        "HPA 能解决所有推理服务的弹性问题。",
        "Horizontal Pod Autoscaler is the universal strategy.",
        "所有推理服务都应使用水平 Pod 自动扩缩容。",
        "仅 HPA 可以解决推理服务弹性。",
        "推理服务只用 HPA 就能完成弹性治理。",
        "推理服务弹性使用 HPA 就够。",
        "推理服务弹性使用 HPA 就够了。",
        "HPA alone solves inference elasticity.",
        "Use only HPA for inference elasticity.",
        "Use HPA only for inference elasticity.",
        "HPA is enough for inference elasticity.",
        "HPA 不能感知模型冷启动，但所有推理服务都应只用它。",
    ):
        assert cloud_native_contains_forbidden_tutorial_or_hpa(prohibited)

    for allowed in (
        "HPA 并非所有推理服务的万能策略。",
        "不要仅靠 HPA。",
        "HPA 不能单独解决推理服务弹性。",
        "Do not rely on HPA alone.",
        "HPA alone is not enough.",
    ):
        assert not cloud_native_contains_forbidden_tutorial_or_hpa(allowed)


def test_cloud_native_hpa_guard_rejects_postpositive_exclusivity() -> None:
    assert cloud_native_contains_forbidden_tutorial_or_hpa(
        "HPA only works for elasticity"
    )


def test_cloud_native_hpa_guard_allows_clause_level_negation() -> None:
    for allowed in (
        "仅靠 HPA 是不够的",
        "Only HPA is not enough",
        "HPA is not a universal strategy",
    ):
        assert not cloud_native_contains_forbidden_tutorial_or_hpa(allowed)


def test_llm_agent_observability_draft_meets_contract(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        COMPLETED_BATCH_ARTICLES
    )

    path = batch_article_path("llm-agent-observability")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == (
        "LLM / Agent 可观测性：从调用链到质量闭环"
    )
    assert frontmatter["slug"] == "llm-agent-observability"
    assert tuple(frontmatter["tags"]) == ("LLM", "Agent", "可观测性")
    assert frontmatter["draft"] is True
    assert frontmatter["author"] == AUTHOR
    assert frontmatter["motto"] == MOTTO
    assert frontmatter["publishedAt"] == TODAY
    assert frontmatter["updatedAt"] == TODAY
    assert markdown.lstrip().startswith("## ")
    assert AUTHOR not in markdown
    assert MOTTO not in markdown

    diagrams = tuple(re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown))
    assert len(diagrams) == 3
    assert tuple(
        re.search(r"(?m)^\s*accTitle:\s*(.+?)\s*$", diagram).group(1)
        for diagram in diagrams
    ) == (
        "LLM Agent 端到端证据链",
        "AI 可观测性分层信号模型",
        "线上反馈到离线评估闭环",
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
        len(mermaid_principal_node_ids(diagram)) <= 12
        for diagram in diagrams
    )

    for required_topic in (
        "trace",
        "模型版本",
        "Prompt 版本",
        "token",
        "延迟",
        "成本",
        "检索证据",
        "工具调用",
        "质量评估",
        "反馈集",
    ):
        assert required_topic.casefold() in markdown.casefold()
    for required_link in (
        "../foundations/llm-application-system-architecture",
        "rag-retrieval-engineering",
        "../agent-architecture/enterprise-agent-system-architecture",
    ):
        assert re.search(rf"\]\((?:\./)?{re.escape(required_link)}\)", markdown)

    assert "Development" in markdown
    assert "可演进映射" in markdown
    assert "内部证据模型" in markdown
    assert observability_has_effective_context_contract(markdown)
    assert observability_has_call_level_usage_contract(markdown)
    context_node = re.search(r"\b(\w+)\[上下文装配[^\]]*\]", diagrams[0])
    effective_node = re.search(r"\b(\w+)\[有效上下文[^\]]*\]", diagrams[0])
    model_node = re.search(r"\b(\w+)\[模型调用[^\]]*\]", diagrams[0])
    assert context_node and effective_node and model_node
    assert re.search(
        rf"(?m)^\s*{context_node.group(1)}\s*-->\s*"
        rf"{effective_node.group(1)}\[",
        diagrams[0],
    )
    assert re.search(
        rf"(?m)^\s*{effective_node.group(1)}\s*-->\s*"
        rf"{model_node.group(1)}\[",
        diagrams[0],
    )
    assert "usage 必须按每次模型调用记录" in markdown
    assert "重试调用也生成独立记录并计入执行级汇总" in markdown
    assert "跨模型比较优先使用实付成本/有效结果" in markdown
    assert "测量窗口 `W`" in markdown
    assert "资格任务集合 `Q`" in markdown
    assert "`W` 内 `Q` 的全部执行相关实付成本" in markdown
    assert "`W` 内 `Q` 通过质量与时限门禁的有效结果数" in markdown
    assert not observability_contains_fixed_genai_schema_claim(markdown)
    assert not observability_contains_duplicate_rag_or_agent_tutorial(markdown)

    candidate_index = validate_completed_batch_as_publication_candidates(
        tmp_path
    )
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)


def test_observability_contract_guards_schema_and_tutorial_boundaries() -> None:
    for prohibited in (
        "gen_ai.usage.input_tokens 是永久字段合同。",
        "OpenTelemetry GenAI 属性已经是稳定字段合同。",
        "gen_ai.request.model provides a permanent schema contract.",
        "## RAG 算法实现\nHNSW 的完整教程。",
        "HNSW、IVF 和 BM25 的参数选择如下。",
        "## Agent 状态机\n逐项解释所有状态。",
        "ReAct、Plan-and-Execute 和 WaitingApproval 的转换如下。",
    ):
        assert (
            observability_contains_fixed_genai_schema_claim(prohibited)
            or observability_contains_duplicate_rag_or_agent_tutorial(prohibited)
        )

    for allowed in (
        "gen_ai.usage.input_tokens 不是永久字段合同。",
        "OpenTelemetry GenAI 字段不能视为固定 schema。",
        "本文只记录检索证据与工具调用结果，不重述对应实现。",
    ):
        assert not observability_contains_fixed_genai_schema_claim(allowed)
        assert not observability_contains_duplicate_rag_or_agent_tutorial(allowed)


def test_observability_context_and_usage_guards_reject_execution_only_summary() -> None:
    execution_only = """
conversation_id: conv-7
versions:
  resolved_model: model-family/revision
  prompt_template: support-answer-v12
usage:
  input_tokens: 1800
  output_tokens: 260
"""
    assert not observability_has_effective_context_contract(execution_only)
    assert not observability_has_call_level_usage_contract(execution_only)

    complete_contract = """
effective_context:
  assembly_rule_version: context-v8
  task_state_snapshot_ref: state://task/42/v7
  prompt_messages_ref: evidence://prompt/42
  retrieval_evidence_refs: [ev-17]
  tool_result_refs: [tool-result-81]
  input_token_budget: 32000
  used_input_tokens: 2400
  truncated: false
  content_digest: sha256:abc
model_calls:
  - call_id: call-1
    resolved_model: model-family/revision
    usage_source: provider_response
    billing_or_tokenizer_basis: provider/revision
    retry_of: null
    input_tokens: 2400
    output_tokens: 260
    cache_read_tokens: 0
    cache_write_tokens: 0
    reasoning_tokens: null
execution_usage:
  source_call_ids: [call-1]
"""
    assert observability_has_effective_context_contract(complete_contract)
    assert observability_has_call_level_usage_contract(complete_contract)


def test_open_source_runtime_draft_meets_contract(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        "agent-identity-access-control",
        "llm-inference-serving-engineering",
        "ai-cloud-native-runtime",
        "llm-agent-observability",
        "open-source-agent-runtime",
    )

    path = batch_article_path("open-source-agent-runtime")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == (
        "Hermes 与 OpenClaw：开源 Agent 运行时的设计边界"
    )
    assert frontmatter["slug"] == "open-source-agent-runtime"
    assert tuple(frontmatter["tags"]) == ("Agent", "开源运行时", "架构分析")
    assert frontmatter["draft"] is True
    assert frontmatter["author"] == AUTHOR
    assert frontmatter["motto"] == MOTTO
    assert frontmatter["publishedAt"] == TODAY
    assert frontmatter["updatedAt"] == TODAY
    assert markdown.lstrip().startswith("## ")
    assert AUTHOR not in markdown
    assert MOTTO not in markdown

    diagrams = tuple(re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown))
    assert len(diagrams) == 3
    assert tuple(
        re.search(r"(?m)^\s*accTitle:\s*(.+?)\s*$", diagram).group(1)
        for diagram in diagrams
    ) == (
        "开源 Agent 运行时共同循环",
        "Hermes 与 OpenClaw 能力责任映射",
        "Agent 运行时能力边界",
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
        len(mermaid_principal_node_ids(diagram)) <= 12
        for diagram in diagrams
    )
    assert "官方可核验能力" in diagrams[1]
    assert "本文责任抽象" in diagrams[1]

    for required_topic in (
        "Agent loop",
        "context",
        "session",
        "Skills",
        "Tools",
        "memory",
        "sandbox",
        "recovery",
        "ownership boundary",
        "provider（模型/API 提供方）",
        "model（模型）",
        "agent runtime（Agent 运行时）",
        "channel（消息渠道）",
    ):
        assert required_topic.casefold() in markdown.casefold()

    for evidence_grade in (
        "官方文档明示",
        "公开代码直接证明",
        "从公开结构可以推断",
        "本文推断",
    ):
        assert evidence_grade in markdown

    assert (
        "源稿旧称 `Clawdbot` 仅用于项目更名核验；"
        "当前项目名是 OpenClaw。"
    ) in markdown
    assert markdown.count("Clawdbot") == 1
    assert "渠道只负责消息入口、回复路由与平台协议适配" in markdown
    assert "远程执行必须回到运行时的工具与权限边界" in markdown
    assert re.search(
        r"\]\(\.\./agent-architecture/agent-identity-access-control\)",
        markdown,
    )
    assert re.search(
        r"\]\(\.\./ai-engineering/llm-agent-observability\)",
        markdown,
    )
    assert not open_source_runtime_uses_legacy_name_as_current(markdown)
    assert not open_source_runtime_contains_dynamic_ranking(markdown)
    assert not open_source_runtime_contains_unmarked_inference(markdown)
    assert not open_source_runtime_confuses_layers(markdown)

    candidate_index = validate_completed_batch_as_publication_candidates(tmp_path)
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 11


def test_open_source_runtime_guards_reject_forbidden_claims() -> None:
    prohibited = (
        "Clawdbot 的当前运行时以 Gateway 为核心。",
        "Hermes 有 40+ 个工具并且 Star 数排名第一。",
        "它的目录说明运行时一定会自动恢复，这个推断就是实现事实。",
        "provider 就是 model。",
        "agent runtime 等同于 channel。",
    )
    guards = (
        open_source_runtime_uses_legacy_name_as_current,
        open_source_runtime_contains_dynamic_ranking,
        open_source_runtime_contains_unmarked_inference,
        open_source_runtime_confuses_layers,
    )
    for claim in prohibited:
        assert any(guard(claim) for guard in guards)


def test_open_source_runtime_guards_allow_precise_boundaries() -> None:
    allowed = (
        "源稿旧称 `Clawdbot` 仅用于项目更名核验；当前项目名是 OpenClaw。",
        "本文不按工具数量、渠道数量或社区热度排名。",
        "从公开结构可以推断：责任边界比功能清单更稳定。",
        "本文推断：渠道与运行时分层有利于故障定位。",
        "provider 解决认证与模型目录；model 是本轮所选模型；agent runtime 执行循环；channel 承载消息。",
    )
    for claim in allowed:
        assert not open_source_runtime_uses_legacy_name_as_current(claim)
        assert not open_source_runtime_contains_dynamic_ranking(claim)
        assert not open_source_runtime_contains_unmarked_inference(claim)
        assert not open_source_runtime_confuses_layers(claim)
