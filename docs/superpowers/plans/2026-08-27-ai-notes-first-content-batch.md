# AI 工程笔记首批内容迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精读并清洗个人博客中的五篇 AI 文章，将它们作为当前平台 AI 工程笔记的首批正式内容发布。

**Architecture:** 个人博客仓库只作为只读原稿来源；每篇文章逐段完成人工内容审计，在目标仓库形成不依赖原站的 Markdown 副本。现有后端内容索引、发布校验和前端阅读器保持不变；新增生产内容契约测试锁定五篇文章的分类、元数据、正文结构和旧标记防护。

**Tech Stack:** Markdown、YAML frontmatter、Python 3.11、pytest、FastAPI 内容仓库、React Markdown、Mermaid、Vite/Vitest。

## Global Constraints

- 只能迁移主题本身属于大模型、Agent、RAG、AI 开发工具或 AI 工程实践的文章。
- 每篇源文必须从头到尾精读，并按章节作出“保留、改写、删除、核验”判断；关键词扫描只能防漏，不能代替内容判断。
- 源仓库 `/Users/neo/Developer/personal/starship-blog-source` 保持只读，不建立符号链接、构建依赖或运行时连接。
- 删除旧公司、旧项目、人员、客户、内部链接、旧站品牌、旧组织语境和历史发布时间；不得用虚构的新公司经历替换。
- 易变化的技术事实仅使用迁移当日的官方文档、规范或一手研究论文核验；无法确认的事实删除，工程推断必须明确标注。
- 阅读页单独渲染文章标题，正文不得再包含一级标题；不得包含原始 HTML。
- Markdown 表格、代码块、链接、标题锚点和 Mermaid 必须在现有阅读器中可用。
- 五篇文章 `publishedAt` 与 `updatedAt` 固定为当前平台首次内部发布日期 `2026-08-27`。
- 每篇文章先以 `draft: true` 完成清洗和校验，达到标准后改为 `draft: false`，独立提交。
- 工作区现有未跟踪文件属于其他会话，不得添加、修改或删除。

---

## File map

- `backend/app/ai_notes/legacy_markers.yaml`：阻止已确认的旧站和个人品牌标记进入已发布文章。
- `backend/app/ai_notes/README.md`：更新首次迁移后的内容维护说明。
- `backend/tests/test_ai_notes_production_content.py`：锁定生产目录中的五篇文章、元数据和正文结构。
- `backend/app/ai_notes/content/01-foundations/01-agent-systems-handbook.md`：Agent 系统综合手册。
- `backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md`：企业 Agent 系统架构。
- `backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md`：Claude Code 公开能力与架构启发。
- `backend/app/ai_notes/content/04-ai-engineering/01-vector-databases-and-rag.md`：向量数据库与 RAG 原理和工程设计。
- `backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md`：AI 辅助架构设计方法。

### Task 1: 建立生产内容契约与旧标记门禁

**Files:**
- Create: `backend/tests/test_ai_notes_production_content.py`
- Modify: `backend/app/ai_notes/legacy_markers.yaml`
- Modify: `backend/app/ai_notes/README.md`

**Interfaces:**
- Consumes: `validate_publication(root: Path, marker_file: Path, *, today: date) -> AiNotesIndex`
- Produces: `published_article(category_slug: str, article_slug: str) -> AiNoteArticle` 测试辅助函数
- Produces: `assert_clean_body(markdown: str) -> None` 测试辅助函数

- [ ] **Step 1: 写旧标记与生产正文结构的失败测试**

创建测试文件并写入以下骨架：

```python
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
```

- [ ] **Step 2: 运行测试，确认它因旧标记清单为空而失败**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py`

Expected: `test_legacy_marker_file_covers_identified_source_branding` FAIL，实际值为 `{"markers": []}`。

- [ ] **Step 3: 更新旧标记清单**

将 `legacy_markers.yaml` 改为：

```yaml
markers:
  - inkbot.cn
  - Ink Blog
  - STARSHIP
  - 星舰
```

- [ ] **Step 4: 更新内容维护说明**

保留现有迁移流程，删除 README 中“当前空清单只适用于零迁移文章阶段”的陈述，改为说明清单已经包含首批迁移确认的旧站与个人品牌标记，后续发现新标记时必须继续追加。

- [ ] **Step 5: 运行测试与内容校验**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py tests/test_ai_notes_validation.py && .venv/bin/python -m app.ai_notes.validate`

Expected: tests PASS；校验输出 `AI notes content valid: 5 categories, 0 published articles`。

- [ ] **Step 6: 提交门禁**

```bash
git add backend/app/ai_notes/legacy_markers.yaml backend/app/ai_notes/README.md backend/tests/test_ai_notes_production_content.py
git commit -m "test: define first AI notes publication contract"
```

### Task 2: 精读并迁移 Claude Code 架构分析

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Create: `backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md`

**Interfaces:**
- Source: `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/ClaudeCode架构设计理论分析.md`
- Produces: `/ai-notes/tools-and-frameworks/claude-code-architecture`

- [ ] **Step 1: 从头到尾精读 323 行源文**

按顺序阅读全文，不跳过参考来源。对“事实边界、产品形态、权限与工具、Hooks、上下文、MCP、Subagents、可观测性、工程启发、不能断言的部分”逐节判断。重点识别已变更的 Anthropic 文档路径、已经演进的 Hook/配置名称、把公开行为误写成内部实现的段落，以及与 Agent 系统手册重复而不必保留的泛论述。

- [ ] **Step 2: 核验所有产品事实**

只使用 Anthropic 当前官方 Claude Code 文档核验产品能力、权限、Hooks、memory、subagents、MCP 和可观测性；若保留跨产品对照，只使用对应厂商官方文档。记录每个外链的标题、当前 URL 和它支持的具体结论；没有一手来源的内部机制保留为明确的“工程推断”或删除。

- [ ] **Step 3: 先写失败的文章契约测试**

追加：

```python
def test_publishes_clean_claude_code_architecture_note() -> None:
    article = published_article("tools-and-frameworks", "claude-code-architecture")
    assert article.title == "Claude Code 架构分析：公开能力与工程启发"
    assert article.filename == "claude-code-architecture.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.tags == ("Claude Code", "Agent", "AI 开发工具")
    assert_clean_body(article.markdown)
```

- [ ] **Step 4: 运行测试，确认文章缺失失败**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_claude_code_architecture_note`

Expected: FAIL at `assert article is not None`。

- [ ] **Step 5: 创建并清洗草稿**

使用以下 frontmatter，先设 `draft: true`：

```yaml
---
title: Claude Code 架构分析：公开能力与工程启发
slug: claude-code-architecture
description: 基于官方公开能力，分析 Claude Code 的权限、工具、Hooks、上下文、MCP 与可观测性设计。
publishedAt: 2026-08-27
updatedAt: 2026-08-27
tags:
  - Claude Code
  - Agent
  - AI 开发工具
draft: true
---
```

正文不含重复一级标题。保留可核验的公开事实、明确标注的工程推断和对自研 Agent 的可操作启发；删除旧日期口吻、固定能力数量、内部版本猜测、未经证实的性能数字和失效链接。

- [ ] **Step 6: 校验草稿、人工复读并发布**

Run: `cd backend && .venv/bin/python -m app.ai_notes.validate`

Expected: 校验通过，文章仍不出现在已发布目录。随后从目标文件开头到结尾复读一次，检查每个事实是否有来源、每个推断是否有边界、每个段落是否服务于 AI 工程学习；再把 `draft` 改为 `false`。

- [ ] **Step 7: 运行文章测试与校验**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_claude_code_architecture_note && .venv/bin/python -m app.ai_notes.validate`

Expected: PASS；校验显示 1 篇已发布文章。

- [ ] **Step 8: 提交文章**

```bash
git add backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md backend/tests/test_ai_notes_production_content.py
git commit -m "docs: publish Claude Code architecture note"
```

### Task 3: 精读并迁移 AI Native 架构设计方法

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Create: `backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md`

**Interfaces:**
- Source: `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-Native辅助大型架构总体设计-最佳实践.md`
- Produces: `/ai-notes/thinking-and-methods/ai-native-architecture-design`

- [ ] **Step 1: 从头到尾精读 414 行源文**

逐节审读背景案例、四阶段协作模型、AI 的三种角色、选型评估、不确定性、文档质量、决策复盘、配套文档和最终清单。特别标记“两项目、2800 行、18/19 轮、3 天、数百 API、V1 平台”等旧项目叙事，判断其背后的方法论是否可脱离原组织成立。

- [ ] **Step 2: 写失败的文章契约测试**

```python
def test_publishes_clean_ai_native_architecture_design_note() -> None:
    article = published_article("thinking-and-methods", "ai-native-architecture-design")
    assert article.title == "AI Native 辅助架构设计：协作方法与质量控制"
    assert article.filename == "ai-native-architecture-design.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.tags == ("AI Native", "架构设计", "工程方法")
    assert_clean_body(article.markdown)
```

- [ ] **Step 3: 运行测试，确认文章缺失失败**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_ai_native_architecture_design_note`

Expected: FAIL at `assert article is not None`。

- [ ] **Step 4: 创建并清洗草稿**

```yaml
---
title: AI Native 辅助架构设计：协作方法与质量控制
slug: ai-native-architecture-design
description: 总结 AI 参与架构设计时的材料治理、结构搭建、方案推演、决策记录与质量收敛方法。
publishedAt: 2026-08-27
updatedAt: 2026-08-27
tags:
  - AI Native
  - 架构设计
  - 工程方法
draft: true
---
```

删除两个旧项目的身份、规模、周期和版本信息，把可复用经验改写为“适用条件—人的责任—AI 的责任—产物—检查点”。保留四阶段模型、三种 AI 角色和质量清单，但删除自我宣传、效率倍数和无法复核的结果描述。

- [ ] **Step 5: 校验草稿、人工复读并发布**

Run: `cd backend && .venv/bin/python -m app.ai_notes.validate`

Expected: 草稿结构通过。随后逐段复读，确认每个方法都明确输入、产物与人的最终责任，不再能反推出旧项目；把 `draft` 改为 `false`。

- [ ] **Step 6: 运行文章测试与校验**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_ai_native_architecture_design_note && .venv/bin/python -m app.ai_notes.validate`

Expected: PASS；校验显示 2 篇已发布文章。

- [ ] **Step 7: 提交文章**

```bash
git add backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md backend/tests/test_ai_notes_production_content.py
git commit -m "docs: publish AI Native architecture design note"
```

### Task 4: 精读并迁移企业级 Agent 系统架构

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Create: `backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md`

**Interfaces:**
- Source: `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/企业级Agent系统架构设计-从循环引擎到信任层级.md`
- Produces: `/ai-notes/agent-architecture/enterprise-agent-system-architecture`

- [ ] **Step 1: 从头到尾精读 1495 行源文**

按“系统定义、架构全景、框架选型、核心引擎、子 Agent、安全、能力接入、支撑系统、新旧共存、测试、实施路线”逐节审读。对每张图、每个状态、每个信任级别和每个框架判断是通用设计、旧项目约束、当前产品事实还是作者建议。重点删除“真实项目、数百 API、2800 行、19 轮、3 天”、原平台 V1/V2 和客户旅程等组织线索。

- [ ] **Step 2: 核验架构引用**

Agent 工具调用、MCP、安全与评估只引用当前官方规范、厂商文档或一手安全框架。框架能力表如果不能逐项由官方文档支持，就改成按抽象类型讲选型，不保留横向打分和固定功能数量。

- [ ] **Step 3: 写失败的文章契约测试**

```python
def test_publishes_clean_enterprise_agent_architecture_note() -> None:
    article = published_article("agent-architecture", "enterprise-agent-system-architecture")
    assert article.title == "企业级 Agent 系统架构：从循环引擎到信任层级"
    assert article.filename == "enterprise-agent-system-architecture.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.tags == ("Agent", "系统架构", "AI 工程")
    assert_clean_body(article.markdown)
```

- [ ] **Step 4: 运行测试，确认文章缺失失败**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_enterprise_agent_architecture_note`

Expected: FAIL at `assert article is not None`。

- [ ] **Step 5: 创建并清洗草稿**

```yaml
---
title: 企业级 Agent 系统架构：从循环引擎到信任层级
slug: enterprise-agent-system-architecture
description: 从 Agent 循环、状态管理、工具边界、信任层级、子 Agent、治理与评估构建可落地的系统架构。
publishedAt: 2026-08-27
updatedAt: 2026-08-27
tags:
  - Agent
  - 系统架构
  - AI 工程
draft: true
---
```

保留状态机、停止条件、工具契约、信任层级、Hook、子 Agent 隔离、Prompt 分层、上下文、制品和测试体系。删除旧项目规模与交付叙事；将“Agent 取代 UI”等绝对结论改为带适用条件的架构选择；压缩与综合手册重复的框架百科。

- [ ] **Step 6: 校验草稿、人工复读并发布**

Run: `cd backend && .venv/bin/python -m app.ai_notes.validate`

Expected: 草稿结构通过。随后逐节对照原稿审计结果复读目标稿，确认每张 Mermaid 都能独立解释、术语前后一致、组织线索已抽象、事实与建议分开；把 `draft` 改为 `false`。

- [ ] **Step 7: 运行文章测试与校验**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_enterprise_agent_architecture_note && .venv/bin/python -m app.ai_notes.validate`

Expected: PASS；校验显示 3 篇已发布文章。

- [ ] **Step 8: 提交文章**

```bash
git add backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md backend/tests/test_ai_notes_production_content.py
git commit -m "docs: publish enterprise Agent architecture note"
```

### Task 5: 精读并迁移向量数据库与 RAG

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Create: `backend/app/ai_notes/content/04-ai-engineering/01-vector-databases-and-rag.md`

**Interfaces:**
- Source: `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/向量数据库与RAG-深度理论知识.md`
- Produces: `/ai-notes/ai-engineering/vector-databases-and-rag`

- [ ] **Step 1: 从头到尾精读 1899 行源文**

按“向量基础、ANN 算法、Embedding、RAG、分块、评估、混合检索、知识图谱、GraphRAG、数据库架构、选型与资源”逐节阅读，并逐个检查公式、复杂度、示例、性能数字、产品对比和参考链接。删除 GPT-4 旧知识截止日期、虚构公司营收、未经来源支持的精度/QPS/成本表，以及把产品营销口径写成通用事实的内容。

- [ ] **Step 2: 使用一手资料核验技术事实**

HNSW、FAISS、原始 RAG、BM25/融合方法和 GraphRAG 使用原论文或维护方官方资料；数据库产品能力只引用各自当前官方文档。若产品横向对比无法在相同版本和测试条件下成立，则改写为选型维度，不保留排名或绝对数值。

- [ ] **Step 3: 写失败的文章契约测试**

```python
def test_publishes_clean_vector_database_and_rag_note() -> None:
    article = published_article("ai-engineering", "vector-databases-and-rag")
    assert article.title == "向量数据库与 RAG：原理、检索与工程设计"
    assert article.filename == "vector-databases-and-rag.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.tags == ("RAG", "向量数据库", "检索")
    assert_clean_body(article.markdown)
```

- [ ] **Step 4: 运行测试，确认文章缺失失败**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_vector_database_and_rag_note`

Expected: FAIL at `assert article is not None`。

- [ ] **Step 5: 创建并清洗草稿**

```yaml
---
title: 向量数据库与 RAG：原理、检索与工程设计
slug: vector-databases-and-rag
description: 从向量表示和近似最近邻检索出发，讲清 RAG、混合检索、评估与生产选型的工程边界。
publishedAt: 2026-08-27
updatedAt: 2026-08-27
tags:
  - RAG
  - 向量数据库
  - 检索
draft: true
---
```

正文以“问题—原理—工程取舍—评估”组织，不做产品百科。公式保留变量定义，图表标明前提，示例使用中性文档集合；结论明确区分离线检索指标、生成质量指标与线上业务指标。

- [ ] **Step 6: 校验草稿、人工复读并发布**

Run: `cd backend && .venv/bin/python -m app.ai_notes.validate`

Expected: 草稿结构通过。随后逐段检查公式、数字、术语和引用，确认没有旧时间案例、公司数据或无前提性能结论；把 `draft` 改为 `false`。

- [ ] **Step 7: 运行文章测试与校验**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_vector_database_and_rag_note && .venv/bin/python -m app.ai_notes.validate`

Expected: PASS；校验显示 4 篇已发布文章。

- [ ] **Step 8: 提交文章**

```bash
git add backend/app/ai_notes/content/04-ai-engineering/01-vector-databases-and-rag.md backend/tests/test_ai_notes_production_content.py
git commit -m "docs: publish vector database and RAG note"
```

### Task 6: 精读并迁移 Agent 系统手册

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Create: `backend/app/ai_notes/content/01-foundations/01-agent-systems-handbook.md`

**Interfaces:**
- Source: `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Agent学习系统手册-从概念到工程实践.md`
- Produces: `/ai-notes/foundations/agent-systems-handbook`

- [ ] **Step 1: 从头到尾精读 2452 行源文**

按十二章顺序完整阅读：学习路径、最小闭环、架构全景、运行时、工具与能力、上下文/RAG/记忆、治理与安全、多 Agent/技能/进化、观测与评估、落地路线、框架选型、文档生命周期。逐个判断产品案例、框架描述、行业安全结论和二十二条总结是否仍由正文支持。与已迁移的企业 Agent 架构稿逐节比对，保留“学习地图和概念边界”，删除重复的详细方案。

- [ ] **Step 2: 核验综合手册中的易变事实**

Claude Code、MCP、OpenAI Agents、LangGraph、CrewAI、AutoGen、Dify 等产品或框架只引用当前官方资料。对 IAM、Prompt Injection、评估和高风险行动引用官方标准或一手安全资料。不能稳定核验的产品功能表改成抽象选型问题。

- [ ] **Step 3: 写失败的文章契约测试**

```python
def test_publishes_clean_agent_systems_handbook() -> None:
    article = published_article("foundations", "agent-systems-handbook")
    assert article.title == "Agent 系统手册：从概念到工程实践"
    assert article.filename == "agent-systems-handbook.md"
    assert article.published_at == TODAY
    assert article.updated_at == TODAY
    assert article.tags == ("Agent", "AI 工程", "系统设计")
    assert_clean_body(article.markdown)
```

- [ ] **Step 4: 运行测试，确认文章缺失失败**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_agent_systems_handbook`

Expected: FAIL at `assert article is not None`。

- [ ] **Step 5: 创建并清洗草稿**

```yaml
---
title: Agent 系统手册：从概念到工程实践
slug: agent-systems-handbook
description: 系统梳理 Agent 的最小闭环、运行时、工具、上下文、治理、评估与工程落地路线。
publishedAt: 2026-08-27
updatedAt: 2026-08-27
tags:
  - Agent
  - AI 工程
  - 系统设计
draft: true
---
```

将文章定位为基础学习地图：先定义 Agent，再沿执行链路解释运行时、工具、上下文、治理和评估，最后给出落地与选型检查清单。压缩产品案例和框架百科；删除 `Last reviewed: 2026-06-01` 等旧时间、绝对化成熟度判断、未经验证的内部机制，以及与企业架构稿重复的详细状态机和信任层级方案。

- [ ] **Step 6: 校验草稿、人工复读并发布**

Run: `cd backend && .venv/bin/python -m app.ai_notes.validate`

Expected: 草稿结构通过。随后完整复读目标稿，核对章节之间没有重复或矛盾、所有框架事实仍有来源、总结中的每个判断都能回指正文；把 `draft` 改为 `false`。

- [ ] **Step 7: 运行文章测试与校验**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_publishes_clean_agent_systems_handbook && .venv/bin/python -m app.ai_notes.validate`

Expected: PASS；校验显示 5 篇已发布文章。

- [ ] **Step 8: 提交文章**

```bash
git add backend/app/ai_notes/content/01-foundations/01-agent-systems-handbook.md backend/tests/test_ai_notes_production_content.py
git commit -m "docs: publish Agent systems handbook"
```

### Task 7: 全批次交叉审校、渲染验证与回归

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Modify only if review finds defects: the five article files from Tasks 2–6

**Interfaces:**
- Produces: exactly five published production articles across exactly five categories

- [ ] **Step 1: 写完整清单的失败测试**

在最终交叉审校开始前追加：

```python
def test_first_batch_is_exactly_the_approved_five_articles() -> None:
    index = validate_publication(CONTENT_ROOT, MARKER_FILE, today=TODAY)
    actual = {
        category.slug: tuple(article.slug for article in category.articles)
        for category in index.categories
    }
    assert actual == {
        "foundations": ("agent-systems-handbook",),
        "agent-architecture": ("enterprise-agent-system-architecture",),
        "tools-and-frameworks": ("claude-code-architecture",),
        "ai-engineering": ("vector-databases-and-rag",),
        "thinking-and-methods": ("ai-native-architecture-design",),
    }
```

暂时将任意一篇目标文章设为 `draft: true`，运行该测试并确认它因对应分类为空而失败；立即恢复 `draft: false`，不得提交人为失败状态。

- [ ] **Step 2: 运行清单测试并恢复绿色**

Run: `cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_first_batch_is_exactly_the_approved_five_articles`

Expected: PASS。

- [ ] **Step 3: 做五篇文章的交叉精读**

按目录顺序连续阅读五篇目标稿。检查跨文重复、术语冲突、不同文章对 Agent/RAG/工具/评估的定义是否一致，并逐个打开外链。保留各篇边界：手册负责学习地图，企业架构负责方案深度，Claude Code 负责公开产品分析，RAG 负责检索工程，AI Native 负责协作方法。

- [ ] **Step 4: 做自动防漏扫描**

Run: `rg -n -i 'inkbot|starship|星舰|last reviewed|最后更新|真实项目|2800|19 轮|18 轮|3 天|2026-0[1-4]-|2025-01-21' backend/app/ai_notes/content --glob '*.md' --glob '!_index.md'`

Expected: 无输出。扫描结果只用于发现遗漏；任何命中必须回到上下文人工判断，不得直接批量替换。

- [ ] **Step 5: 运行后端内容与 API 回归**

Run: `cd backend && .venv/bin/python -m app.ai_notes.validate && .venv/bin/pytest -q tests/test_ai_notes_production_content.py tests/test_ai_notes_repository.py tests/test_ai_notes_validation.py tests/test_ai_notes_api.py`

Expected: 校验显示 `5 categories, 5 published articles`；全部测试 PASS。

- [ ] **Step 6: 运行全量后端与前端验证**

Run: `cd backend && .venv/bin/pytest -q`

Expected: 全量后端测试 PASS。

Run: `cd webui && npm test -- --run && npm run build`

Expected: 全量前端测试 PASS；TypeScript 与 Vite 生产构建成功。

- [ ] **Step 7: 在认证后的本地页面做视觉检查**

从 Agent 大脑首页点击“AI 工程笔记”，确认左侧五类各一篇；依次打开五篇，检查标题不重复、目录滚动、表格横向滚动、代码高亮、外链、标题锚点、Mermaid 和移动抽屉。任何单图错误不得阻断全文阅读。

- [ ] **Step 8: 提交最终清单与审校修复**

```bash
git add backend/tests/test_ai_notes_production_content.py backend/app/ai_notes/content
git commit -m "test: verify first AI notes content batch"
```

- [ ] **Step 9: 请求代码与内容审查**

审查范围限定为本计划新增或修改的内容文件、旧标记、README 和生产内容测试。审查者必须重点核对：是否精读而非机械替换、是否仍残留旧组织语境、事实/推断边界、五篇之间的重复度，以及 Markdown 渲染风险。
