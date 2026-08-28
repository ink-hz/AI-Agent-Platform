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
    "metabot-agent-control-bus",
    "agent-framework-selection",
    "intent-driven-ai-business-platform",
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
    "https://github.com/openclaw/openclaw/blob/468054f93c431bfe192327f439efe325be52f2b4/docs/concepts/agent-runtimes.md": (
        "selected runtime 接收 prepared turn、驱动模型输出、处理 native tool calls 并返回 finished turn"
    ),
    "https://github.com/openclaw/openclaw/blob/468054f93c431bfe192327f439efe325be52f2b4/docs/agent-runtime-architecture.md": (
        "布局本身不证明 selected runtime 拥有 Gateway session、平台策略或恢复编排"
    ),
}

METABOT_SOURCE_REVIEW_STATUS = {
    "MetaBot架构设计理论分析.md": (
        "已精读：1-515",
        "远程控制、渠道适配、消息桥、持久执行与恢复问题框架",
        "旧组织、旧拓扑、代码规模、固定数量、端口、版本与营销结论",
        "已核验：两个当前代码仓与 Platform relay 边界（2026-08-28）",
        "通用运行循环留在既有篇；本篇只写远程控制、可靠投递与状态所有权",
    ),
}

FRAMEWORK_SELECTION_SOURCE_REVIEW_STATUS = {
    "主流Agent框架深度分析-从架构本质到生产可用性.md": (
        "已精读：1-323",
        "产品形态、生产责任维度与选型问题框架",
        "旧候选集、功能表、宣传语、成熟度判断与永久排名",
        "已逐页核验：8 个官方入口（2026-08-28）",
        "三篇同类文章留下具体架构；本篇只给出选型方法与退出条件",
    ),
}

INTENT_PLATFORM_SOURCE_REVIEW_STATUS = {
    "干掉用户旅程-意图驱动的业务平台架构设计.md": (
        "已精读：1-379",
        "第1-4、10章：固定旅程边界、能力原子化、受控编排与渐进迁移问题框架",
        "标题与第2-9章：唯一对话入口、UI消失、完全自治、自演进取代发布及营销结论",
        "已核验：Anthropic、NIST、OpenAI PDF 与 Agents 文档（2026-08-28）",
        "信任全景留在企业 Agent；AI 协作方法留在 AI Native；本篇只写受控意图执行",
    ),
}

FRAMEWORK_SELECTION_PRIMARY_SOURCES = {
    "https://docs.langchain.com/oss/python/langgraph/overview": (
        "低层编排框架与运行时，聚焦持久执行、流式输出、人在回路与持久化；页面未给出明确 lifecycle 状态"
    ),
    "https://docs.crewai.com/index": (
        "以 Agents、Crews 和 Flows 构建协作与编排，部署和 RBAC 等能力属于 Enterprise journey；页面未给出明确 lifecycle 状态"
    ),
    "https://learn.microsoft.com/en-us/agent-framework/overview/": (
        "Microsoft Agent Framework 是 AutoGen 与 Semantic Kernel 的直接继任者；Go 版明确为 public preview"
    ),
    "https://developers.openai.com/api/docs/guides/agents": (
        "代码优先的 Agents SDK 运行 agent loop，服务器仍拥有部署、工具实现、状态存储与审批决策；页面未给出明确 lifecycle 状态"
    ),
    "https://adk.dev/": (
        "旧 Google ADK 入口已重定向 adk.dev；当前定位覆盖多语言开发框架、Agent Runtime、部署、观测与评估，页面未给出统一 lifecycle 状态"
    ),
    "https://docs.dify.ai/en/home": (
        "开源 AI 应用平台，覆盖 Agent、工作流、聊天应用、Web/API 发布与云端或自托管；页面未给出明确 lifecycle 状态"
    ),
    "https://github.com/coze-dev/coze-studio": (
        "开源一站式可视化 Agent 开发平台；公网部署需评估安全风险，开源版与商业版存在能力差异"
    ),
    "https://code.claude.com/docs/en/agent-sdk/overview": (
        "Python/TypeScript 库在自有进程运行 Claude Code agent loop；托管长任务属于独立 Managed Agents 产品，页面未给出 SDK 明确 lifecycle 状态"
    ),
}

INTENT_PLATFORM_PRIMARY_SOURCES = {
    "https://www.anthropic.com/research/building-effective-agents": (
        "workflows 使用预定义代码路径，agents 动态决定过程与工具；从简单方案开始，只在结果证明必要时增加复杂度"
    ),
    "https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence": (
        "按组织风险容忍度治理，明确人的监督责任，并持续监测部署后的风险控制"
    ),
    "https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf": (
        "编排应渐进增加复杂度，guardrails 采用分层防御，失败阈值与高风险行动触发人工介入"
    ),
    "https://developers.openai.com/api/docs/guides/agents": (
        "代码优先的 Agents SDK 运行 agent loop，服务器仍拥有部署、工具实现、状态存储与审批决策"
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


def mermaid_has_white_group(source: str) -> bool:
    group_ids = set(
        re.findall(
            r"(?mi)^\s*subgraph\s+([A-Za-z_][A-Za-z0-9_-]*)\b",
            source,
        )
    )
    white_style_ids = set(
        re.findall(
            r"(?mi)^\s*style\s+([A-Za-z_][A-Za-z0-9_-]*)\s+"
            r"[^\n]*\bfill:#FFFFFF\b",
            source,
        )
    )
    return bool(group_ids) and group_ids <= white_style_ids


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


def open_source_runtime_contains_recovery_absolutism(markdown: str) -> bool:
    structure_cue = re.compile(r"(?:目录(?:结构)?|代码(?:结构)?|仓库(?:结构)?)")
    recovery_cue = re.compile(r"(?:恢复|续跑)")
    absolute_cue = re.compile(r"(?:所有|全部|一定|必然|完全无感|自动恢复)")
    explicit_limit = re.compile(r"(?:不能|不足以|不等于|并不|无法)")
    for clause in re.split(r"[\n。！？；;]", markdown):
        if explicit_limit.search(clause):
            continue
        if (
            structure_cue.search(clause)
            and recovery_cue.search(clause)
            and absolute_cue.search(clause)
        ):
            return True
    return False


def open_source_runtime_confuses_openclaw_ownership(markdown: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:selected\s+)?(?:agent\s+)?runtime\s*"
            r"(?:拥有|负责|管理|维护)[^。；;\n]{0,48}"
            r"(?:gateway\s+session|channel\s+delivery|Gateway\s*会话|渠道投递)",
            markdown,
        )
    )


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


def metabot_contains_forbidden_legacy_or_scale_claim(markdown: str) -> bool:
    prohibited_patterns = (
        r"(?i)xvirobotics|XVI\s+Robotics",
        r"(?i)(?<![\w.])v\d+\.\d+\.\d+(?![\w.])",
        r"(?i)\bLOC\b|代码行数|lines?\s+of\s+code",
        r"(?i)(?:端口|port)\s*[:：]?\s*\d{2,5}\b",
        r"(?i)(?<![\w.])\d+\s*(?:个|名|台|种|条)?\s*"
        r"(?:Agents?|Bots?|进程|用户|渠道|实例|部署节点)\b",
    )
    return any(re.search(pattern, markdown) for pattern in prohibited_patterns)


def metabot_claim_clauses(markdown: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in re.split(
            r"[\n。！？；;,，]|(?:但是|但|然而|却|不过|可是)",
            markdown,
        )
        if clause.strip()
    )


def metabot_contains_design_as_implementation(markdown: str) -> bool:
    explicit_limit = re.compile(
        r"(?:不能|不可|不应|并非|不是|不等于|尚未|未被|没有)"
    )
    for clause in metabot_claim_clauses(markdown):
        if explicit_limit.search(clause):
            continue
        if re.search(
            r"(?:设计稿|规划文档|架构设计)[^。；;\n]{0,48}"
            r"(?:证明|表明|说明)[^。；;\n]{0,48}"
            r"(?:已经|已)[^。；;\n]{0,24}(?:实现|上线|运行)",
            clause,
        ):
            return True
    return False


def metabot_contains_boundary_absolutism(markdown: str) -> bool:
    prohibited_patterns = (
        r"(?:渠道(?:消息)?身份|sender\s+identity|chatId|session\s+key|会话绑定)"
        r"[^。；;\n]{0,40}(?:等同于|就是|自动成为)"
        r"[^。；;\n]{0,24}(?:平台授权身份|授权|权限)",
        r"(?:\back\b|\baccepted\b|消息确认|接收确认|已接收)"
        r"[^。；;\n]{0,32}(?:等同于|就是|代表|证明)"
        r"[^。；;\n]{0,24}(?:命令完成|任务完成|执行成功|完成|成功)",
        r"(?:重连|重新连接|断线恢复)[^。；;\n]{0,40}"
        r"(?:自动|可以|应当)[^。；;\n]{0,24}(?:重放|重试)"
        r"[^。；;\n]{0,16}(?:所有|全部|命令|副作用)",
        r"(?:未知|不确定)[^。；;\n]{0,16}副作用[^。；;\n]{0,32}"
        r"(?:自动|直接|继续)[^。；;\n]{0,16}(?:重试|重放|重复)",
    )
    explicit_limit = re.compile(
        r"(?:不能|不可|不应|不得|并非|不是|不等于|并不|无法|没有|"
        r"尚未|未曾|未被|未能|"
        r"≠|do\s+not|cannot|can't|is\s+not|does\s+not)",
        re.IGNORECASE,
    )
    for clause in metabot_claim_clauses(markdown):
        if explicit_limit.search(clause):
            continue
        if any(re.search(pattern, clause, re.IGNORECASE) for pattern in prohibited_patterns):
            return True
    return False


def metabot_contains_unlabelled_inference(markdown: str) -> bool:
    allowed_markers = ("从当前公开结构可以推断", "本文推断")
    inference_cues = re.compile(
        r"(?:由此可见|显然|这说明|可以断定|可以认为|必然意味着)"
    )
    return any(
        inference_cues.search(line)
        and not any(marker in line for marker in allowed_markers)
        for line in markdown.splitlines()
    )


def framework_selection_claim_clauses(markdown: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in re.split(
            r"[\n。！？；;,]|(?:并且|而且|且|但是|但|然而|却|不过|可是|"
            r"所以|因此|因而|故而|\b(?:and|but|however|then|therefore)\b)",
            markdown,
            flags=re.IGNORECASE,
        )
        if clause.strip()
    )


def framework_selection_violation_is_locally_negated(
    clause: str,
    violation: re.Match[str],
) -> bool:
    prefix = clause[max(0, violation.start() - 40):violation.start()]
    matched = violation.group(0)
    suffix = clause[violation.end():violation.end() + 20]
    negating_predicate_prefix = re.compile(
        r"(?i)(?:(?:不应|不得|不要|不能|不可)"
        r"(?:被)?(?:称为|写成|视为|当作|宣称为|认为是|列为|排为)|"
        r"并非|不是|没有|不存在|不做|拒绝|避免|禁止|"
        r"not|never|cannot|can't|should\s+not\s+(?:be\s+)?"
        r"(?:called|described|ranked)|must\s+not\s+(?:be\s+)?"
        r"(?:called|described|ranked)|do\s+not\s+(?:compare|rank|score))"
        r"[^\n。！？；;,，]{0,28}$"
    )
    negated_relation_to_target = re.compile(
        r"(?i)(?:(?:不等于|不代表|不意味着|不能证明)\s*"
        r"(?:已证明\s*)?|(?:并非|并不是|不是|不写成|不得写成|"
        r"不应写成)\s*|(?:is\s+not|isn't|does\s+not\s+mean)\s*)"
        r"(?:生产可用|已成熟|稳定(?:承诺)?|production[- ]ready|stable)$"
    )
    negating_predicate_suffix = re.compile(
        r"(?i)^\s*(?:并不成立|并不存在|不存在|"
        r"不成立|is\s+not\s+claimed)"
    )
    negated_ranking_enumeration = re.compile(
        r"(?i)(?:不比较|不做比较)\s*"
        r"(?:(?:stars?|Star\s*数|星级|评分|总分|象限|永久排名|"
        r"排名(?:第)?|最佳|最好)\s*(?:、|，|或|和|与|以及)\s*)*$"
    )
    return bool(
        negating_predicate_prefix.search(prefix)
        or negated_relation_to_target.search(matched)
        or negating_predicate_suffix.search(suffix)
        or negated_ranking_enumeration.search(clause[:violation.start()])
    )


def framework_selection_contains_forbidden_ranking_or_maturity(
    markdown: str,
) -> bool:
    prohibited_patterns = (
        r"(?:最佳|最好|第一|唯一正确|永久排名|排名第|"
        r"best\s+framework|only\s+correct|permanent\s+ranking)",
        r"(?:\bstars?\b|Star\s*数|星级|评分|总分|象限)",
    )
    maturity_patterns = (
        r"(?:页面|文档|官网|项目页)[^\n。；]{0,32}"
        r"(?:存在|可访问|已上线)[^\n。；]{0,32}"
        r"(?:证明|代表|等于|意味着)[^\n。；]{0,24}"
        r"(?:生产可用|已成熟|稳定|production[- ]ready)",
        r"(?:preview|public\s+preview|experimental|预览|实验性)"
        r"[^\n。；]{0,32}(?:就是|已是|等同于|稳定承诺|"
        r"stable|production[- ]ready)",
    )
    for clause in framework_selection_claim_clauses(markdown):
        for pattern in (*prohibited_patterns, *maturity_patterns):
            for violation in re.finditer(pattern, clause, re.IGNORECASE):
                if not framework_selection_violation_is_locally_negated(
                    clause,
                    violation,
                ):
                    return True
    return False


def framework_selection_has_four_product_forms(markdown: str) -> bool:
    forms = (
        "developer tool",
        "orchestration library / SDK",
        "agent runtime",
        "end-to-end platform",
    )
    normalized = markdown.casefold()
    return all(form.casefold() in normalized for form in forms)


def framework_selection_has_eight_responsibility_dimensions(markdown: str) -> bool:
    dimensions = (
        "control flow",
        "state persistence",
        "tool / permission",
        "recovery",
        "evaluation",
        "deployment",
        "observability",
        "team ownership",
    )
    normalized = markdown.casefold()
    return all(dimension.casefold() in normalized for dimension in dimensions)


def intent_platform_claim_clauses(markdown: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in re.split(
            r"[\n。！？；;,，]|(?:但是|但|然而|却|不过|可是|"
            r"所以|因此|同时|并且|而且|仍然|仍|"
            r"\b(?:but|however|yet|therefore|meanwhile)\b)",
            markdown,
            flags=re.IGNORECASE,
        )
        if clause.strip()
    )


def intent_platform_ui_claim_clauses(markdown: str) -> tuple[str, ...]:
    immediate_scope = re.compile(
        r"(?!.*(?:不在|并非|不是|超出|以外|之外))"
        r"(?:在)?[^\n。！？；;,，]{0,12}试点(?:范围)?内"
    )
    sections = re.split(
        r"[\n。！？；;]|(?:但是|但|然而|却|不过|可是|"
        r"所以|因此|同时|并且|而且|仍然|仍|"
        r"\b(?:but|however|yet|therefore|meanwhile)\b)",
        markdown,
        flags=re.IGNORECASE,
    )
    clauses: list[str] = []
    for section in sections:
        segments = tuple(
            segment.strip()
            for segment in re.split(r"[,，]", section)
            if segment.strip()
        )
        for index, segment in enumerate(segments):
            if index and immediate_scope.fullmatch(segments[index - 1]):
                clauses.append(f"{segments[index - 1]}，{segment}")
            else:
                clauses.append(segment)
    return tuple(clauses)


def intent_platform_contains_forbidden_absolutism(markdown: str) -> bool:
    ui_prohibited_patterns = (
        r"(?:消灭|取代|淘汰|移除|取消|删除)[^。；;\n]{0,20}"
        r"(?:所有|全部)?\s*(?:UI|页面|表单|界面)",
        r"(?:所有|全部)?\s*(?:UI|页面|表单|界面)"
        r"[^。；;\n]{0,20}(?:全部|都|完全)?\s*(?:会|将|要)?\s*"
        r"(?:消失|被取代|被消灭)",
    )
    prohibited_patterns = (
        r"(?:规则|确定性流程)[^。；;\n]{0,24}"
        r"(?:全部|都|完全)\s*(?:会|将|要)?\s*(?:消失|被取代|被移除)",
        r"(?:完全|无限)\s*自治",
        r"(?:无需|无须|不需要)[^。；;\n]{0,16}"
        r"(?:人类|人工|审批|治理|human)",
        r"(?:自我进化|自演进)[^。；;\n]{0,24}取代[^。；;\n]{0,16}发布流程",
        r"(?:所有|全部)业务[^。；;\n]{0,20}"
        r"(?:交给|委托给|由)\s*Agent(?:\s*处理)?",
        r"(?:模型自述|模型说|HTTP\s*200|消息已发送|流程到末节点)"
        r"[^。；;\n]{0,24}(?:等同于|代表|证明|就是)[^。；;\n]{0,20}"
        r"(?:完成|成功)",
        r"自然语言[^。；;\n]{0,20}(?:等同于|就是|代表)"
        r"[^。；;\n]{0,16}(?:完整的?)?意图合同",
    )
    direct_negation = re.compile(
        r"(?:不会|不能|不可|不得|不应|不要|并非|不是|无法|"
        r"不能用|并不|没有(?:实现|达到)?|"
        r"do\s+not|does\s+not|cannot|can't|is\s+not|isn't)\s*$",
        re.IGNORECASE,
    )
    internal_negation = re.compile(
        r"(?:不会|不能|不可|不得|不应|不要|并非|不是|不等于|"
        r"不代表|不意味着|无法|没有|并不)"
        r"[^。；;\n]{0,6}(?:消灭|取代|淘汰|移除|取消|删除|消失|"
        r"自治|完成|成功|意图合同)",
        re.IGNORECASE,
    )
    evidentiary_negation = re.compile(
        r"(?:没有|缺乏)(?:足够|充分)?(?:证据|依据)"
        r"(?:能够|可以|足以)?(?:证明|表明|支持)"
        r"[^\n。！？；;,，]{0,12}$"
    )
    scoped_ui_prefix = re.compile(
        r"(?:试点(?:范围)?内|局部|特定|限定|部分|某个|某些|当前)"
        r"[^。；;\n]{0,8}$"
    )

    def is_locally_negated(clause: str, violation: re.Match[str]) -> bool:
        prefix = clause[max(0, violation.start() - 12):violation.start()]
        return bool(
            direct_negation.search(prefix)
            or evidentiary_negation.search(clause[:violation.start()])
            or internal_negation.search(violation.group(0))
        )

    for clause in intent_platform_ui_claim_clauses(markdown):
        for pattern in ui_prohibited_patterns:
            for violation in re.finditer(pattern, clause, re.IGNORECASE):
                if is_locally_negated(clause, violation):
                    continue
                prefix = clause[:violation.start()]
                has_global_quantifier = bool(
                    re.search(r"(?:所有|全部|一切)", violation.group(0))
                )
                if not has_global_quantifier and scoped_ui_prefix.search(prefix):
                    continue
                return True
    for clause in intent_platform_claim_clauses(markdown):
        for pattern in prohibited_patterns:
            for violation in re.finditer(pattern, clause, re.IGNORECASE):
                if is_locally_negated(clause, violation):
                    continue
                return True
    return False


def intent_platform_has_structured_intent_contract(markdown: str) -> bool:
    fields = (
        "目标",
        "对象",
        "约束",
        "权限主体",
        "风险等级",
        "预算 / 时限",
        "完成条件",
        "证据要求",
        "可撤销 / 补偿边界",
    )
    return all(field in markdown for field in fields)


def intent_platform_has_capability_catalog_contract(markdown: str) -> bool:
    fields = (
        "capability_id:",
        "input_schema:",
        "output_schema:",
        "authorization:",
        "risk_level:",
        "idempotency:",
        "side_effects:",
        "slo:",
        "owner:",
        "evidence:",
        "version:",
    )
    normalized = markdown.casefold()
    return all(field.casefold() in normalized for field in fields)


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
            **METABOT_SOURCE_REVIEW_STATUS,
            **FRAMEWORK_SELECTION_SOURCE_REVIEW_STATUS,
            **INTENT_PLATFORM_SOURCE_REVIEW_STATUS,
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
        COMPLETED_BATCH_ARTICLES
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
    for ownership_marker in (
        "prepared turn",
        "selected agent runtime",
        "native tool calls",
        "OpenClaw host/Gateway",
        "session 与 channel delivery",
        "策略与恢复编排",
        "投影、镜像或集成",
    ):
        assert ownership_marker in markdown
    for diagram_marker in (
        "OpenClaw host / Gateway",
        "selected agent runtime",
        "session 与 channel delivery",
        "策略与恢复编排",
        "prepared turn",
        "native tool calls",
        "合同拥有的 thread/context/tools/compaction",
    ):
        assert diagram_marker in diagrams[2]
    assert "agent runtime ownership boundary" not in diagrams[2]
    assert "有界重试" not in markdown
    assert "失败后在后续访问继续尝试" in markdown
    assert "指数退避间隔封顶" in markdown
    assert "长任务一定会丢失 ownership boundary" not in markdown
    assert "/blob/main/" not in markdown
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
    assert not open_source_runtime_contains_recovery_absolutism(markdown)
    assert not open_source_runtime_confuses_openclaw_ownership(markdown)
    assert not open_source_runtime_confuses_layers(markdown)

    candidate_index = validate_completed_batch_as_publication_candidates(tmp_path)
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)


def test_open_source_runtime_guards_reject_forbidden_claims() -> None:
    prohibited = (
        "Clawdbot 的当前运行时以 Gateway 为核心。",
        "Hermes 有 40+ 个工具并且 Star 数排名第一。",
        "它的目录说明运行时一定会自动恢复，这个推断就是实现事实。",
        "目录结构证明所有任务一定会恢复。",
        "runtime 拥有 Gateway session 和 channel delivery。",
        "provider 就是 model。",
        "agent runtime 等同于 channel。",
    )
    guards = (
        open_source_runtime_uses_legacy_name_as_current,
        open_source_runtime_contains_dynamic_ranking,
        open_source_runtime_contains_recovery_absolutism,
        open_source_runtime_confuses_openclaw_ownership,
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
        "OpenClaw host/Gateway 负责 session 与 channel delivery、策略与恢复编排；selected agent runtime 接收 prepared turn、驱动 model loop、处理 native tool calls，并按合同拥有 canonical thread/context/tools/compaction。",
    )
    for claim in allowed:
        assert not open_source_runtime_uses_legacy_name_as_current(claim)
        assert not open_source_runtime_contains_dynamic_ranking(claim)
        assert not open_source_runtime_contains_recovery_absolutism(claim)
        assert not open_source_runtime_confuses_openclaw_ownership(claim)
        assert not open_source_runtime_confuses_layers(claim)


def test_metabot_source_review_records_current_code_snapshots() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    section_header = "## metabot-agent-control-bus 精读结论"

    assert section_header in review
    section = source_review_h2_section(review, section_header)
    for sha in (
        "94e1c128f33a153b980ef45b8c002d5bb8d2bac9",
        "73e172192e21621c4bb1d9bf307ab8755ac643cf",
    ):
        assert sha in section
    for evidence_grade in (
        "当前代码直接证明",
        "已提交文档明示",
        "作者工程推断",
    ):
        assert evidence_grade in section
    for evidence_path in (
        "deploy/metabot.runtime-contract.json",
        "scripts/reliability/recovery.mjs",
        "flywheel/migrations/003_api.sql",
        "src/feishu/event-handler.ts",
        "src/telegram/telegram-bot.ts",
        "src/wechat/wechat-bot.ts",
        "src/types.ts",
        "src/bridge/message-bridge.ts",
        "src/bridge/error-classifiers.ts",
        "src/bridge/prompt-normalizer.ts",
        "src/session/session-registry.ts",
        "src/engines/claude/session-manager.ts",
        "src/engines/claude/executor-registry.ts",
        "src/engines/claude/persistent-executor.ts",
        "src/api/routes/core-chat-session-store.ts",
        "src/api/routes/core-chat-routes.ts",
        "src/bridge/provider-turn-recovery.ts",
        "src/bridge/claude-turn-recovery.ts",
        "src/reliability/probe-receipt-store.ts",
        "src/utils/audit-logger.ts",
        "src/flywheel/envelope.ts",
        "backend/app/agent_brain/adapters/metabot_local.py",
        "backend/app/execution_relay/models.py",
        "backend/app/execution_relay/repository.py",
        "backend/app/execution_relay/worker.py",
        "backend/app/execution_relay/metabot_client.py",
    ):
        assert evidence_path in section
    assert "当前 collaboration v3 链路没有贯通 Platform 验证主体" in section
    assert "公共任务与 relay 状态" in section
    assert "私有会话与执行状态" in section
    assert "目标系统真实副作用" in section
    assert "provider/process-exit 路径经过 tool-effect gate" in section
    assert "legacy stale-session/context-overflow fallback" in section
    assert "尚未统一经过 effect gate" in section
    assert "可能在已有副作用后重放原 prompt" in section


def test_metabot_agent_control_bus_draft_meets_contract(tmp_path: Path) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        COMPLETED_BATCH_ARTICLES
    )

    path = batch_article_path("metabot-agent-control-bus")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == "MetaBot 架构：Agent 的多渠道远程控制总线"
    assert frontmatter["slug"] == "metabot-agent-control-bus"
    assert tuple(frontmatter["tags"]) == ("Agent", "MetaBot", "远程控制")
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
        "MetaBot 多渠道 Agent 控制平面",
        "远程命令持久状态机",
        "断线恢复与幂等闭环",
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
    assert all(len(mermaid_principal_node_ids(diagram)) <= 12 for diagram in diagrams)

    for required_topic in (
        "channel adapter",
        "message normalization",
        "identity/session binding",
        "persistent executor",
        "command lifecycle",
        "idempotency",
        "reconnect",
        "audit",
        "remote-control risk",
        "IncomingMessage",
        "MessageBridge",
        "SessionManager",
        "CoreChatSessionStore",
        "ExecutorRegistry",
        "core_chat_collaboration_v3",
        "messageSeq",
        "sha256",
    ):
        assert required_topic.casefold() in markdown.casefold()

    for evidence_grade in (
        "当前代码直接证明",
        "已提交文档明示",
        "从当前公开结构可以推断",
        "本文推断",
    ):
        assert evidence_grade in markdown

    for boundary in (
        "渠道消息身份 ≠ 平台授权身份",
        "会话绑定 ≠ 授权",
        "消息确认 ≠ 命令完成",
        "重新连接 ≠ 可以盲目重放",
        "不确定副作用不得自动重复",
        "Platform 持有公共任务与 relay 状态",
        "MetaBot 持有私有会话与执行状态",
        "目标系统持有真实副作用",
        "当前 collaboration v3 请求没有贯通 Platform 验证主体",
    ):
        assert boundary in markdown

    for state_marker in (
        "`active`",
        "`stopped`",
        "`failed`",
        "`accepted`",
        "`replayed`",
        "取消请求",
        "stop 回执",
        "结果不确定",
    ):
        assert state_marker in markdown

    assert not metabot_contains_forbidden_legacy_or_scale_claim(markdown)
    assert not metabot_contains_design_as_implementation(markdown)
    assert not metabot_contains_boundary_absolutism(markdown)
    assert not metabot_contains_unlabelled_inference(markdown)

    candidate_index = validate_completed_batch_as_publication_candidates(tmp_path)
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)


def test_metabot_recovery_discloses_legacy_effect_gate_gap() -> None:
    _, markdown = parse_frontmatter(batch_article_path("metabot-agent-control-bus"))

    for current_fact in (
        "provider / process-exit recovery 已经过 tool-effect gate",
        "legacy stale-session / context-overflow fallback 尚未统一经过 effect gate",
        "可能在已有副作用后重放原 prompt",
        "安全目标是让所有 replay 进入统一证据门禁",
        "无法证明只读或无副作用时停止重放并进入对账",
        "multiple tool_result",
        "部分恢复判断",
    ):
        assert current_fact in markdown

    assert "执行侧恢复更保守" not in markdown
    assert "当前 turn recovery 会先检查" not in markdown

    recovery_diagram = tuple(
        re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown)
    )[2]
    for diagram_marker in (
        "journal 去重",
        "provider / process-exit",
        "effect gate",
        "legacy stale / context fallback",
        "当前缺口",
        "目标：统一 evidence gate",
    ):
        assert diagram_marker in recovery_diagram
    assert len(mermaid_principal_node_ids(recovery_diagram)) <= 12


def test_metabot_audit_does_not_promote_channel_identity() -> None:
    _, markdown = parse_frontmatter(batch_article_path("metabot-agent-control-bus"))

    assert (
        "audit 适合回答哪个已观察到的渠道标识从哪个会话触发了什么控制动作，"
        "但这不是 Platform 验证主体"
    ) in markdown
    assert "audit 适合回答谁从哪个会话触发" not in markdown


def test_metabot_guards_reject_forbidden_claims() -> None:
    prohibited = (
        "xvirobotics 的旧拓扑当前仍然有效。",
        "message-bridge.ts 有 2753 LOC。",
        "服务运行在 port 9100。",
        "当前部署了 9 个 Bot。",
        "设计稿证明这条恢复链已经上线。",
        "渠道消息身份就是平台授权身份。",
        "会话绑定自动成为授权。",
        "accepted 代表任务完成。",
        "消息确认等同于命令完成。",
        "重新连接后可以重放所有命令。",
        "未知副作用可以自动重试。",
        "渠道身份不等于平台授权身份，但会话绑定就是授权",
        "设计稿不能证明 A，但架构设计说明该能力已经上线",
        "由此可见，所有命令都会安全恢复。",
    )
    guards = (
        metabot_contains_forbidden_legacy_or_scale_claim,
        metabot_contains_design_as_implementation,
        metabot_contains_boundary_absolutism,
        metabot_contains_unlabelled_inference,
    )
    for claim in prohibited:
        assert any(guard(claim) for guard in guards), claim


def test_metabot_guards_allow_precise_boundaries() -> None:
    allowed = (
        "渠道消息身份 ≠ 平台授权身份；会话绑定 ≠ 授权。",
        "accepted 不是命令完成，只是接收确认。",
        "重新连接 ≠ 可以盲目重放，不确定副作用不得自动重复。",
        "设计稿不能证明能力已经实现，本文只采用当前代码证据。",
        "渠道身份不等于平台授权身份，但会话绑定也不等于授权。",
        "设计稿不能证明 A，不过当前代码路径 B 直接证明 B 已实现。",
        "从当前公开结构可以推断：三层状态需要分别对账。",
        "本文推断：恢复策略应按副作用类别收窄。",
    )
    for claim in allowed:
        assert not metabot_contains_forbidden_legacy_or_scale_claim(claim)
        assert not metabot_contains_design_as_implementation(claim)
        assert not metabot_contains_boundary_absolutism(claim)
        assert not metabot_contains_unlabelled_inference(claim)


def test_framework_selection_source_review_records_current_official_boundaries() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    section_header = "## agent-framework-selection 精读结论"

    section = source_review_h2_section(review, section_header)
    assert "访问日期：2026-08-28" in section
    for url, supported_claim in FRAMEWORK_SELECTION_PRIMARY_SOURCES.items():
        assert url in section
        assert supported_claim in section


def test_agent_framework_selection_draft_meets_contract(tmp_path: Path) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        COMPLETED_BATCH_ARTICLES
    )

    path = batch_article_path("agent-framework-selection")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == "主流 Agent 框架选型：从开发工具到生产运行时"
    assert frontmatter["slug"] == "agent-framework-selection"
    assert tuple(frontmatter["tags"]) == ("Agent", "框架选型", "工程决策")
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
        "Agent 产品形态与责任边界",
        "生产级 Agent 能力矩阵",
        "Agent 框架选型决策树",
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
    assert all(len(mermaid_principal_node_ids(diagram)) <= 12 for diagram in diagrams)
    assert all(mermaid_has_white_group(diagram) for diagram in diagrams)
    for diagram in (diagrams[0], diagrams[2]):
        for semantic_class, fill in (
            ("tool", "DBEAFE"),
            ("library", "EDE9FE"),
            ("runtime", "DCFCE7"),
            ("platform", "FEF3C7"),
        ):
            assert re.search(
                rf"(?m)^\s*classDef\s+{semantic_class}\s+fill:#{fill}\b",
                diagram,
            )
    decision_diagram = diagrams[2]
    for decision_marker in (
        "终端 / IDE 直接辅助开发",
        "嵌入应用或服务",
        "code-first",
        "可视化交付",
        "跨形态运行时检查",
        "agent runtime 责任边界",
    ):
        assert decision_marker in decision_diagram
    assert "主要操作者" not in decision_diagram
    assert re.search(r"(?m)^\s*class\s+P\s+platform\s*;", decision_diagram)
    assert framework_selection_has_four_product_forms(markdown)
    assert framework_selection_has_eight_responsibility_dimensions(markdown)

    for candidate in (
        "LangGraph",
        "CrewAI",
        "Microsoft Agent Framework",
        "OpenAI Agents SDK",
        "Google ADK",
        "Dify",
        "Coze Studio",
        "Claude Agent SDK",
    ):
        assert candidate in markdown
    for linked_slug in (
        "claude-code-architecture",
        "open-source-agent-runtime",
        "metabot-agent-control-bus",
    ):
        assert re.search(rf"\]\((?:\./)?{linked_slug}\)", markdown)
    for boundary in (
        "框架提供机制 ≠ 团队责任被外包",
        "数据/状态可迁移性",
        "扩展点",
        "运行环境",
        "退出成本",
        "PoC 门禁",
    ):
        assert boundary in markdown
    assert not framework_selection_contains_forbidden_ranking_or_maturity(markdown)

    candidate_index = validate_completed_batch_as_publication_candidates(tmp_path)
    assert sum(
        len(category.articles) for category in candidate_index.categories
    ) == 6 + len(COMPLETED_BATCH_ARTICLES)


def test_framework_selection_guards_reject_rankings_and_false_maturity() -> None:
    prohibited = (
        "LangGraph 是最佳框架。",
        "CrewAI 排名第一。",
        "Coze Studio 有五星评分。",
        "这张象限图给出永久排名。",
        "项目页可访问就证明已成熟。",
        "public preview 就是 stable 承诺。",
        "本文不做最佳框架，但 LangGraph 排名第一。",
        "LangGraph 排名第一且不会改变",
        "public preview 已是 stable 并且不需要复核",
        "不应忽略复核风险所以 LangGraph 排名第一",
        "页面存在就代表 production-ready。",
        "页面存在不等于文档齐全，仍代表 production-ready。",
        "本文不比较 Star 数，LangGraph 排名第一。",
        "本文不比较社区热度仍将 LangGraph 排名第一。",
        "页面存在不等于文档齐全仍代表 production-ready。",
    )
    for claim in prohibited:
        assert framework_selection_contains_forbidden_ranking_or_maturity(claim), claim


def test_framework_selection_guards_allow_time_boundaries_and_uncertainty() -> None:
    allowed = (
        "本文不比较 Star 数、评分或永久排名。",
        "本文不比较 Star 数，评分或永久排名。",
        "页面存在不等于已证明生产可用。",
        "Go 版截至 2026-08-28 明确为 public preview，不得写成稳定承诺。",
        "截至 2026-08-28，官方页面未给出明确 lifecycle 状态。",
        "官方未说明成熟度，因此保留不确定性。",
        "不应称为最佳框架。",
        "public preview 并不是 stable。",
        "页面存在不等于 production-ready。",
    )
    for claim in allowed:
        assert not framework_selection_contains_forbidden_ranking_or_maturity(claim), claim


def test_framework_selection_dimension_guards_fail_closed() -> None:
    four_forms = (
        "developer tool; orchestration library / SDK; agent runtime; "
        "end-to-end platform"
    )
    eight_dimensions = (
        "control flow; state persistence; tool / permission; recovery; "
        "evaluation; deployment; observability; team ownership"
    )
    assert framework_selection_has_four_product_forms(four_forms)
    assert not framework_selection_has_four_product_forms(
        four_forms.replace("agent runtime", "")
    )
    assert framework_selection_has_eight_responsibility_dimensions(eight_dimensions)
    assert not framework_selection_has_eight_responsibility_dimensions(
        eight_dimensions.replace("team ownership", "")
    )


def test_framework_selection_diagram_contract_rejects_width_and_missing_style() -> None:
    compact_styled = """
flowchart LR
    subgraph GROUP[决策]
        A[需求] --> B[责任]
    end
    classDef input fill:#DBEAFE,stroke:#60A5FA,color:#172033;
    class A input;
    style GROUP fill:#FFFFFF,stroke:#CBD5E1,color:#172033;
"""
    too_wide = "\n".join(
        ["flowchart LR"]
        + [f"    N{index}[节点{index}]" for index in range(13)]
        + ["    classDef input fill:#DBEAFE,stroke:#60A5FA,color:#172033;"]
    )
    partially_white = """
flowchart LR
    subgraph FIRST[第一组]
        A[输入] --> B[处理]
    end
    subgraph SECOND[第二组]
        C[检查] --> D[输出]
    end
    style FIRST fill:#FFFFFF,stroke:#CBD5E1,color:#172033;
"""
    fully_white = partially_white + (
        "    style SECOND fill:#FFFFFF,stroke:#CBD5E1,color:#172033;\n"
    )
    assert len(mermaid_principal_node_ids(compact_styled)) <= 12
    assert re.search(r"(?m)^\s*classDef\s+", compact_styled)
    assert mermaid_has_white_group(compact_styled)
    assert not mermaid_has_white_group(partially_white)
    assert mermaid_has_white_group(fully_white)
    assert len(mermaid_principal_node_ids(too_wide)) > 12
    assert not re.search(r"(?m)^\s*classDef\s+", "flowchart LR\nA --> B")
    assert not mermaid_has_white_group(
        "flowchart LR\nsubgraph GROUP[决策]\nA --> B\nend"
    )


def test_intent_platform_source_review_records_official_boundaries() -> None:
    review = SOURCE_REVIEW.read_text(encoding="utf-8")
    section_header = "## intent-driven-ai-business-platform 精读结论"
    section = source_review_h2_section(review, section_header)

    assert "已精读：1-379" in section
    assert "3a165ec7de6b712d9cbbc999ee6d7752b9954691f904f41a80d289bc0585d52b" in section
    assert "访问日期：2026-08-28" in section
    assert "PDF 共 34 页" in section
    assert "页面 13、25、31" in section
    for url, supported_claim in INTENT_PLATFORM_PRIMARY_SOURCES.items():
        assert url in section
        assert supported_claim in section


def test_intent_platform_source_review_section_stops_at_next_h2() -> None:
    fixture = """
## intent-driven-ai-business-platform 精读结论
只属于本节
## 后续任务
不属于本节
"""
    section = source_review_h2_section(
        fixture,
        "## intent-driven-ai-business-platform 精读结论",
    )
    assert "只属于本节" in section
    assert "不属于本节" not in section


def test_intent_driven_ai_business_platform_draft_meets_contract(
    tmp_path: Path,
) -> None:
    completed_articles = assert_completed_batch_drafts()
    assert tuple(article.slug for article in completed_articles) == (
        COMPLETED_BATCH_ARTICLES
    )

    path = batch_article_path("intent-driven-ai-business-platform")
    frontmatter, markdown = parse_frontmatter(path)
    assert frontmatter["title"] == "意图驱动的 AI 业务平台：从固定旅程到受控执行"
    assert frontmatter["slug"] == "intent-driven-ai-business-platform"
    assert tuple(frontmatter["tags"]) == ("AI Native", "业务平台", "意图驱动")
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
        "意图驱动 AI 业务平台分层",
        "用户意图到完成证据执行链",
        "业务平台渐进演进路线",
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
    assert all(len(mermaid_principal_node_ids(diagram)) <= 12 for diagram in diagrams)
    assert all(mermaid_has_white_group(diagram) for diagram in diagrams)

    assert intent_platform_has_structured_intent_contract(markdown)
    assert intent_platform_has_capability_catalog_contract(markdown)
    for boundary in (
        "自然语言只是入口之一",
        "确定性 workflow",
        "Agent 只处理开放判断",
        "建议",
        "草拟",
        "审批后执行",
        "有限自治",
        "human-in-the-loop",
        "目标系统真实状态",
        "事务回执",
        "可验证交付物",
        "可停",
        "可降级",
        "可回滚",
    ):
        assert boundary in markdown
    assert re.search(
        r"\]\(\.\./agent-architecture/enterprise-agent-system-architecture\)",
        markdown,
    )
    assert re.search(r"\]\(ai-native-architecture-design\)", markdown)
    assert not intent_platform_contains_forbidden_absolutism(markdown)

    candidate_index = validate_completed_batch_as_publication_candidates(tmp_path)
    assert sum(len(category.articles) for category in candidate_index.categories) == 14


def test_intent_platform_guards_reject_absolute_and_mixed_claims() -> None:
    prohibited = (
        "我们将消灭所有 UI。",
        "页面和表单都会消失。",
        "规则和确定性流程都会消失。",
        "系统将实现完全自治。",
        "系统无需人类审批与治理。",
        "自我进化将取代发布流程。",
        "所有业务都交给 Agent。",
        "模型自述代表任务完成。",
        "模型说完成就是真实完成。",
        "HTTP 200 就代表业务完成。",
        "消息已发送就代表业务完成。",
        "流程到末节点就代表任务完成。",
        "自然语言就是完整的意图合同。",
        "我们不会消灭 UI，但页面和表单都会消失。",
        "并非完全自治，但所有业务都交给 Agent。",
        "不能用模型自述证明完成，但 HTTP 200 就代表业务完成。",
        "不应忽略风险，所以系统将实现完全自治。",
        "不会消灭 UI，同时页面和表单都会消失。",
        "没有治理便实现完全自治。",
        "系统不需要人工审批和治理。",
        "全部业务都由 Agent 处理。",
    )
    missed_claims = tuple(
        claim
        for claim in prohibited
        if not intent_platform_contains_forbidden_absolutism(claim)
    )
    assert not missed_claims


def test_intent_platform_guards_allow_explicit_limits() -> None:
    allowed = (
        "意图入口不会消灭 UI。",
        "平台并非完全自治。",
        "不能用模型自述证明完成。",
        "规则和确定性流程不会全部消失。",
        "自然语言只是意图入口之一，不等于结构化意图合同。",
        "消息已发送不代表业务完成。",
        "HTTP 200 不等于完成证据。",
        "试点范围内取代页面入口，其他 UI 继续保留。",
        "在退款试点范围内，取代页面入口，其他 UI 继续保留。",
        "没有证据证明平台会实现完全自治。",
    )
    false_positives = tuple(
        claim
        for claim in allowed
        if intent_platform_contains_forbidden_absolutism(claim)
    )
    assert not false_positives


def test_intent_platform_execution_chain_requires_guarded_restart() -> None:
    _, markdown = parse_frontmatter(
        batch_article_path("intent-driven-ai-business-platform")
    )
    diagrams = tuple(re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown))
    execution_chain = diagrams[1]

    assert 'V -->|"否"| C["停止并记录未知状态"]' in execution_chain
    assert not re.search(r"(?m)^\s*C\s*-->\s*P\s*$", execution_chain)
    assert 'C -. "人工接管" .-> M["人工对账与补偿决策"]' in execution_chain
    assert 'M -. "修正合同后重新提交" .-> I' in execution_chain
    assert re.search(
        r'(?m)^\s*I(?:\["[^\n]+"\])?\s*-->\s*G\b',
        execution_chain,
    )
    assert "只有人工确认并修正合同后，任务才会重新提交" in markdown


def test_intent_platform_contract_helpers_fail_closed() -> None:
    intent_fields = (
        "目标；对象；约束；权限主体；风险等级；预算 / 时限；完成条件；"
        "证据要求；可撤销 / 补偿边界"
    )
    capability_fields = (
        "capability_id: x\ninput_schema: {}\noutput_schema: {}\n"
        "authorization: delegated\nrisk_level: low\nidempotency: required\n"
        "side_effects: none\nslo: defined\nowner: team\nevidence: receipt\n"
        "version: v1"
    )
    assert intent_platform_has_structured_intent_contract(intent_fields)
    assert not intent_platform_has_structured_intent_contract(
        intent_fields.replace("证据要求", "")
    )
    assert intent_platform_has_capability_catalog_contract(capability_fields)
    assert not intent_platform_has_capability_catalog_contract(
        capability_fields.replace("authorization:", "")
    )
