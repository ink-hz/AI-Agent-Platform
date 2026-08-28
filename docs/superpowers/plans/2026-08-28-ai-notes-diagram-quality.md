# AI 工程笔记图示质量升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为已上线的五篇 AI 工程笔记建立统一、可测试的 Mermaid 图示语言，补齐关键架构图和流程图，并把通过真实渲染与移动端检查的版本发布到生产环境。

**Architecture:** 图示语义和颜色保存在 Markdown 的 Mermaid 源码中，沿用现有 `ArticleMarkdown -> MermaidDiagram -> DOMPurify -> data:image/svg+xml` 渲染链，不引入新运行时组件。前端集成测试读取生产 Markdown、调用锁定版本的真实 Mermaid 渲染器并检查清洗结果；后端内容测试锁定文章元数据、关键图示边界和更新时间。

**Tech Stack:** Markdown frontmatter、Mermaid 11.17.2、React 19、Vitest 2.1、DOMPurify 3.4、Python 3.11、pytest、FastAPI 内容仓库。

## Global Constraints

- 质量优先；图数不是目标，图必须比相邻正文更清楚地表达关系。
- 系统全景图使用分层分区色，普通流程图使用固定语义色。
- 语义色固定为：输入 `#DBEAFE/#60A5FA`、模型 `#EDE9FE/#A78BFA`、数据 `#CCFBF1/#5EEAD4`、策略 `#FEF3C7/#F59E0B`、工具 `#DCFCE7/#4ADE80`、成功 `#D1FAE5/#10B981`、风险 `#FEE2E2/#F87171`、中性基础设施 `#F3F4F6/#9CA3AF`。
- 所有节点同时使用文字标签；颜色不能成为唯一信息通道。
- 手机宽度下不可读的复杂图必须拆分，不能依赖无限横向滚动。
- 首批文章的 `publishedAt` 保持 `2026-08-27`，本轮更新后的 `updatedAt` 为 `2026-08-28`。
- 作者保持 `苍渊`，座右铭保持 `博观而约取，厚积而薄发。`。
- 不改变左侧目录树、首页入口、文章 API、署名组件或阅读页布局。
- 个人博客源仓库只读；不得把它加入运行时、构建或部署依赖。
- 不恢复缺乏当前官方依据的产品能力、预测、内部实现推断或旧项目叙事。

---

## File Map

- `backend/app/ai_notes/README.md`：记录图示选型、语义色、分层色和发布检查规则。
- `backend/tests/test_ai_notes_production_content.py`：锁定首批文章发布时间、更新日期和关键图示语义。
- `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`：读取真实生产 Markdown，逐图调用 Mermaid 与 SVG 清洗链。
- `backend/app/ai_notes/content/01-foundations/01-agent-engineering-learning-map.md`：补学习全景图与能力递进图。
- `backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md`：补运行循环、信任决策并统一现有图配色。
- `backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md`：统一现有图配色并补当前公开能力架构图。
- `backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md`：补索引链路、查询链路并统一现有图配色。
- `backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md`：统一协作流程配色并补人机责任边界图。

## Shared Mermaid Test Contract

`MermaidDiagram.integration.test.tsx` 在 Task 1 中增加以下测试帮助函数，后续任务只增加文章级用例：

```tsx
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const CONTENT_ROOT = resolve(process.cwd(), "../backend/app/ai_notes/content");
const SEMANTIC_FILLS = [
  "#DBEAFE", "#EDE9FE", "#CCFBF1", "#FEF3C7",
  "#DCFCE7", "#D1FAE5", "#FEE2E2", "#F3F4F6",
];
let productionDiagramSequence = 0;

function productionArticle(relativePath: string): string {
  return readFileSync(resolve(CONTENT_ROOT, relativePath), "utf8");
}

function mermaidBlocks(markdown: string): string[] {
  return [...markdown.matchAll(/```mermaid\n([\s\S]*?)\n```/g)]
    .map((match) => match[1] ?? "");
}

function expectSemanticStyling(source: string): void {
  expect(source).toMatch(/\b(?:classDef|style)\b/);
  expect(SEMANTIC_FILLS.some((color) => source.includes(color))).toBe(true);
}

async function expectProductionDiagramsToRender(sources: string[]): Promise<void> {
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "neutral",
    htmlLabels: false,
    flowchart: { htmlLabels: false },
  });
  for (const source of sources) {
    expectSemanticStyling(source);
    const rendered = await mermaid.render(
      `ai-note-production-${++productionDiagramSequence}`,
      source,
    );
    const imageSource = mermaidImageSource(rendered.svg);
    const sanitized = decodeURIComponent(imageSource.split(",", 2)[1] ?? "");
    expect(imageSource).toMatch(/^data:image\/svg\+xml;charset=utf-8,/);
    expect(sanitized).toContain("<svg");
    expect(sanitized).not.toContain("<script");
    expect(sanitized).not.toContain("<foreignObject");
  }
}
```

---

### Task 1: 建立图示契约并补强 Agent 工程学习地图

**Files:**
- Modify: `backend/app/ai_notes/README.md`
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`
- Modify: `backend/app/ai_notes/content/01-foundations/01-agent-engineering-learning-map.md`

**Interfaces:**
- Consumes: 现有 `mermaidImageSource(renderedSvg: string): string` 和生产 Markdown 目录。
- Produces: `productionArticle()`、`mermaidBlocks()`、`expectSemanticStyling()`、`expectProductionDiagramsToRender()` 测试帮助函数；两张有配色的学习地图 Mermaid。

- [ ] **Step 1: 写学习地图的失败合同测试**

在后端测试中把校验时钟和首次发布时间拆开，并加入图示断言：

```python
PUBLISHED_ON = date(2026, 8, 27)
TODAY = date(2026, 8, 28)


def mermaid_blocks(markdown: str) -> tuple[str, ...]:
    return tuple(re.findall(r"```mermaid\n([\s\S]*?)\n```", markdown))


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
```

把五个既有文章测试中的 `published_at == TODAY` 改为 `published_at == PUBLISHED_ON`；除学习地图外，其余四篇暂时继续断言 `updated_at == PUBLISHED_ON`。

在前端集成测试中加入 Shared Mermaid Test Contract，并加入：

```tsx
it("renders the styled Agent engineering learning map", async () => {
  const markdown = productionArticle(
    "01-foundations/01-agent-engineering-learning-map.md",
  );
  const sources = mermaidBlocks(markdown);
  expect(sources).toHaveLength(2);
  expect(sources.join("\n")).toContain("最小行动循环");
  expect(sources.join("\n")).toContain("能力递进");
  await expectProductionDiagramsToRender(sources);
});
```

- [ ] **Step 2: 运行测试并确认因缺图和旧更新时间失败**

Run:

```bash
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_learning_map_visualizes_the_system_and_progression
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 后端因当前文章没有 Mermaid 或 `updatedAt` 仍为 `2026-08-27` 失败；前端因 Mermaid 数量为零失败。

- [ ] **Step 3: 在内容维护文档中写入图示规范**

在 `backend/app/ai_notes/README.md` 的文章约束后增加“图示语言”，包含以下可复制基线：

```mermaid
classDef input fill:#DBEAFE,stroke:#60A5FA,color:#172033;
classDef model fill:#EDE9FE,stroke:#A78BFA,color:#172033;
classDef data fill:#CCFBF1,stroke:#5EEAD4,color:#172033;
classDef policy fill:#FEF3C7,stroke:#F59E0B,color:#172033;
classDef tool fill:#DCFCE7,stroke:#4ADE80,color:#172033;
classDef success fill:#D1FAE5,stroke:#10B981,color:#172033;
classDef risk fill:#FEE2E2,stroke:#F87171,color:#172033;
classDef infra fill:#F3F4F6,stroke:#9CA3AF,color:#172033;
```

同一节明确：全景图使用浅色 `subgraph` 分区；流程图使用语义类；公式、命令和协议值保留文本块；图前后必须有正文解释；桌面和手机都需真实检查。

- [ ] **Step 4: 把学习路线改成两张有语义的图**

将 `updatedAt` 改为 `2026-08-28`。把“目标与当前状态”文本链替换为“最小行动循环”图，节点依次包含：目标与状态、模型提出行动、策略与结构校验、工具执行、外部观察、继续/追问/停止；为输入、模型、策略、工具、数据和成功节点应用对应 `classDef`。

把七版调查任务文本链替换为“能力递进”图，按三个浅色分区组织：

```text
基础闭环：固定日志与证据结论 -> 工具搜索真实记录
生产运行：持久任务与故障恢复 -> 规范、历史与检索证据 -> 审批后幂等创建工单
质量进化：真实失败回归集 -> 验证是否需要独立子 Agent
```

分区之间使用带标签的箭头“状态化”“知识化”“评估化”，保留原文对每一阶段验收标准的解释。

- [ ] **Step 5: 运行学习地图合同与内容校验**

Run:

```bash
cd backend && .venv/bin/python -m app.ai_notes.validate
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 内容校验输出 `AI notes content valid: 5 categories, 5 published articles`；后端生产内容测试与前端真实 Mermaid 集成测试全部 PASS。

- [ ] **Step 6: 提交学习地图与图示契约**

```bash
git add backend/app/ai_notes/README.md backend/tests/test_ai_notes_production_content.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx backend/app/ai_notes/content/01-foundations/01-agent-engineering-learning-map.md
git commit -m "docs: establish AI notes diagram language"
```

---

### Task 2: 补强企业级 Agent 系统架构图

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`
- Modify: `backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md`

**Interfaces:**
- Consumes: Task 1 的真实 Mermaid 渲染帮助函数和语义色基线。
- Produces: 有分层色的系统全景、有语义色的状态机与接口关系图，以及运行循环、信任决策两张新图。

- [ ] **Step 1: 写企业架构图的失败测试**

```python
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
```

```tsx
it("renders every enterprise Agent architecture diagram", async () => {
  const sources = mermaidBlocks(productionArticle(
    "02-agent-architecture/01-enterprise-agent-system-architecture.md",
  ));
  expect(sources).toHaveLength(5);
  expect(sources.join("\n")).toContain("运行循环");
  expect(sources.join("\n")).toContain("信任决策");
  await expectProductionDiagramsToRender(sources);
});
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_enterprise_agent_note_visualizes_runtime_and_trust
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 当前只有三张无显式样式的 Mermaid，测试 FAIL。

- [ ] **Step 3: 重构系统全景图与现有图样式**

将 `updatedAt` 改为 `2026-08-28`。系统全景图按“入口与状态、智能决策、控制与执行、证据与观测”四个 `subgraph` 分区；颜色只区分层级，节点仍使用用户、模型、策略、工具、数据、成功和基础设施语义类。

状态机保留 Ready、Running、WaitingApproval、WaitingExternal、Suspended、Completed、Failed、Cancelled 全部状态，为正常状态、等待状态、成功状态和失败状态分别应用中性、策略、成功和风险色。UI/API/Agent 工具关系图为传统 UI、Agent、接口、领域服务和数据应用输入、模型、工具、基础设施和数据语义类。

- [ ] **Step 4: 把运行循环和信任决策文本链改成独立流程图**

运行循环图必须完整包含：读取目标与状态、模型提出行动/完成声明、结构与工具校验、身份/策略/风险计算、允许/拒绝/审批、工具执行、结果写回、完成验证和继续循环。

信任决策图必须完整包含：验证主体与委托、组织级策略、工具与资源授权、参数和上下文风险、允许/拒绝/审批、审批绑定精确行动、执行前复验、执行与审计。拒绝和风险分支使用风险色，审批使用策略色，执行使用工具色，证据使用成功色。

- [ ] **Step 5: 运行文章合同和真实渲染测试**

Run:

```bash
cd backend && .venv/bin/python -m app.ai_notes.validate
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 全部 PASS，五张图均经过真实 Mermaid 与 SVG 清洗链。

- [ ] **Step 6: 提交企业架构图更新**

```bash
git add backend/tests/test_ai_notes_production_content.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md
git commit -m "docs: enrich enterprise Agent architecture diagrams"
```

---

### Task 3: 补强 Claude Code 公开能力架构图

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`
- Modify: `backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md`

**Interfaces:**
- Consumes: Anthropic 当前官方 Claude Code 概览、工具、权限、Hooks、MCP 和界面文档；Task 1 图示契约。
- Produces: 三张经过当前事实核验的 Claude Code 图示。

- [ ] **Step 1: 重新核验图中允许出现的产品事实**

逐一打开 Anthropic 官方 Claude Code 概览、常见工作流、权限、Hooks、MCP、IDE、桌面与 Web 文档。建立“官方明确说明 / 工程抽象 / 不采用”三列审计：入口形态、内置工具、MCP、权限、Hooks、上下文、子 Agent、验证和可观测性只有在当前官方页面支持时才能进入产品能力图；未来生产力预测、内部调用顺序和未公开实现不进入图。

- [ ] **Step 2: 写 Claude Code 图示失败测试**

```python
def test_claude_code_note_visualizes_public_capabilities() -> None:
    article = published_article("tools-and-frameworks", "claude-code-architecture")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 3
    for label in ("公开入口", "上下文", "权限与策略", "内置工具", "MCP", "Hooks", "验证"):
        assert label in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY
```

```tsx
it("renders every Claude Code public architecture diagram", async () => {
  const sources = mermaidBlocks(productionArticle(
    "03-tools-and-frameworks/01-claude-code-architecture.md",
  ));
  expect(sources).toHaveLength(3);
  expect(sources.join("\n")).toContain("公开入口");
  await expectProductionDiagramsToRender(sources);
});
```

- [ ] **Step 3: 运行定向测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_claude_code_note_visualizes_public_capabilities
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 当前只有两张无显式样式的 Mermaid，测试 FAIL。

- [ ] **Step 4: 更新两张现有图并新增公开能力图**

将 `updatedAt` 改为 `2026-08-28`。受控行动循环对用户目标、上下文、模型提案、权限决策、工具执行、反馈、验证和拒绝分支应用对应语义类。

把通用架构图整理成“上下文与编排、权限与 Hooks、工具与 MCP、外部环境、验证与观测”分层图。新增公开能力图，以“公开入口”作为入口分区，以“Agent 工作循环”为核心，连接上下文、权限与策略、内置工具、MCP、Hooks 和验证；图下注明它是基于公开能力的工程分解，不是内部实现还原。

- [ ] **Step 5: 复读正文与图示边界**

确认正文只把官方公开内容写成产品事实；工程抽象明确使用“可以分解为”“对自研系统的启发”等表达。删除或改写与新增图重复的列表，不恢复用户示例中未经本次官方核验的时间线、预测和界面能力。

- [ ] **Step 6: 运行合同、渲染和内容校验**

Run:

```bash
cd backend && .venv/bin/python -m app.ai_notes.validate
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 全部 PASS，三张图真实渲染成功。

- [ ] **Step 7: 提交 Claude Code 图示更新**

```bash
git add backend/tests/test_ai_notes_production_content.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md
git commit -m "docs: clarify Claude Code architecture visually"
```

---

### Task 4: 补强 RAG 索引与查询链路图

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`
- Modify: `backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md`

**Interfaces:**
- Consumes: 现有 RAG 证据流水线、HNSW 与混合检索正文；Task 1 图示契约。
- Produces: 索引链路、查询链路、HNSW 和混合检索四张有语义色的 Mermaid。

- [ ] **Step 1: 写 RAG 图示失败测试**

```python
def test_rag_note_visualizes_index_and_query_pipelines() -> None:
    article = published_article("ai-engineering", "rag-retrieval-engineering")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 4
    for label in ("索引链路", "查询链路", "HNSW", "BM25", "引用校验"):
        assert label in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY
```

```tsx
it("renders every RAG engineering diagram", async () => {
  const sources = mermaidBlocks(productionArticle(
    "04-ai-engineering/01-rag-retrieval-engineering.md",
  ));
  expect(sources).toHaveLength(4);
  expect(sources.join("\n")).toContain("索引链路");
  expect(sources.join("\n")).toContain("查询链路");
  await expectProductionDiagramsToRender(sources);
});
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_rag_note_visualizes_index_and_query_pipelines
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 当前只有两张无显式样式的 Mermaid，测试 FAIL。

- [ ] **Step 3: 新增索引链路和查询链路**

将 `updatedAt` 改为 `2026-08-28`。索引链路必须包含：原始文档、解析、分块、Embedding、向量索引、词法索引、元数据与版本、可选图实体/摘要；用数据色表示制品，用模型色表示 Embedding，用基础设施色表示索引。

查询链路必须包含：用户问题、查询理解与权限上下文、词法/向量候选召回、过滤与融合、重排、上下文组装、基于证据生成、引用校验和结果返回。权限使用策略色，检索使用工具色，证据使用数据色，生成使用模型色，引用校验和结果使用成功色。

- [ ] **Step 4: 为 HNSW 和混合检索图增加语义色**

HNSW 图保留逐层下降与底层 Top-K 逻辑，增加标题节点或图前说明中的“HNSW”；入口和候选为数据色，搜索行动为工具色，层级判断为策略色，Top-K 为成功色。混合检索图中的查询为输入色、BM25/向量召回为工具色、融合/重排为策略色、上下文候选为成功色。

- [ ] **Step 5: 复读避免图文重复和错误承诺**

索引图与查询图分别只解释离线/准实时写入和在线读取，不把 GraphRAG 写成默认必选项，不把某一种 ANN 算法写成所有场景的最优解。公式、证据格式和实验阶梯继续保留文本形式。

- [ ] **Step 6: 运行合同、渲染和内容校验**

Run:

```bash
cd backend && .venv/bin/python -m app.ai_notes.validate
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 全部 PASS，四张图真实渲染成功。

- [ ] **Step 7: 提交 RAG 图示更新**

```bash
git add backend/tests/test_ai_notes_production_content.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md
git commit -m "docs: visualize RAG index and query pipelines"
```

---

### Task 5: 补强 AI Native 人机责任边界图

**Files:**
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`
- Modify: `backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md`

**Interfaces:**
- Consumes: 现有人机协作正文和 Task 1 图示契约。
- Produces: 有阶段色的协作流程图，以及明确人在环的人机责任边界图。

- [ ] **Step 1: 写 AI Native 图示失败测试**

```python
def test_ai_native_note_visualizes_human_ai_responsibility() -> None:
    article = published_article("thinking-and-methods", "ai-native-architecture-design")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 2
    for label in ("AI 辅助", "人负责", "目标与约束", "候选方案", "批准与责任"):
        assert label in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
    assert article.updated_at == TODAY
```

```tsx
it("renders every AI Native collaboration diagram", async () => {
  const sources = mermaidBlocks(productionArticle(
    "05-thinking-and-methods/01-ai-native-architecture-design.md",
  ));
  expect(sources).toHaveLength(2);
  expect(sources.join("\n")).toContain("人负责");
  await expectProductionDiagramsToRender(sources);
});
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py::test_ai_native_note_visualizes_human_ai_responsibility
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 当前只有一张无显式样式的 Mermaid，测试 FAIL。

- [ ] **Step 3: 更新现有协作流程并新增责任边界图**

将 `updatedAt` 改为 `2026-08-28`。现有流程为材料与数据、AI 提取/推演、人类决策、制品与验证应用数据、模型、输入、成功等语义类；风险检查返回问题定义的回路使用风险色。

新增责任边界图，使用两个浅色分区：

```text
AI 辅助：材料提取 -> 冲突发现 -> 候选方案 -> 一致性与风险检查
人负责：目标与约束 -> 取舍与决策 -> 批准与责任 -> 评审与验证
```

图中必须显示人向 AI 提供目标与约束，AI 向人提供候选方案和检查证据，人对最终决策、批准和责任承担闭环；不得画成 AI 自动发布设计。

- [ ] **Step 4: 运行合同、渲染和内容校验**

Run:

```bash
cd backend && .venv/bin/python -m app.ai_notes.validate
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py
cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 全部 PASS，两张图真实渲染成功。

- [ ] **Step 5: 提交 AI Native 图示更新**

```bash
git add backend/tests/test_ai_notes_production_content.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md
git commit -m "docs: define human AI architecture responsibilities"
```

---

### Task 6: 交叉审校、视觉验收与生产发布

**Files:**
- Modify only when review finds a concrete defect: the five article Markdown files, `backend/app/ai_notes/README.md`, `backend/tests/test_ai_notes_production_content.py`, `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Interfaces:**
- Consumes: Tasks 1–5 的五篇更新文章和真实渲染合同。
- Produces: 一致的图示语言、完整测试证据、生产 release SHA 和线上视觉验收结果。

- [ ] **Step 1: 连续复读全部五篇和全部图示**

按目录顺序阅读五篇文章。逐图回答：这张图只解释一个问题吗；颜色是否与跨文章语义一致；节点与连线是否由正文支持；图前后是否说明观察重点；是否存在可以删除的重复文本；是否把推断画成事实。修复任何具体缺陷，不增加装饰性图。

- [ ] **Step 2: 扫描样式、旧标记和危险内容**

Run:

```bash
rg -n '^```mermaid$|classDef|^\s*style ' backend/app/ai_notes/content --glob '*.md'
! rg -n -i 'inkbot|starship|星舰|last reviewed|最后更新|真实项目|2800|19 轮|18 轮|3 天|2026-0[1-4]-|2025-01-21' backend/app/ai_notes/content --glob '*.md' --glob '!_index.md'
! rg -n '^# |<[A-Za-z][^>]*>' backend/app/ai_notes/content --glob '*.md' --glob '!_index.md'
git diff --check
```

Expected: Mermaid 与样式清单可人工逐项对应；两条否定扫描无输出；`git diff --check` 无输出。

- [ ] **Step 3: 运行后端内容与相关测试**

Run:

```bash
cd backend
.venv/bin/python -m app.ai_notes.validate
.venv/bin/pytest -q tests/test_ai_notes_api.py tests/test_ai_notes_production_content.py tests/test_ai_notes_repository.py tests/test_ai_notes_validation.py
```

Expected: 内容校验显示 5 categories / 5 published articles；相关测试全部 PASS。

- [ ] **Step 4: 运行前端定向测试、全量测试和生产构建**

Run:

```bash
cd webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx src/components/ai-notes/ArticleMarkdown.test.tsx src/pages/AiNotesPage.test.tsx
npm test -- --run
npm run build
```

Expected: 定向和全量 Vitest 全部 PASS；TypeScript 与 Vite 生产构建成功。真实 Mermaid 集成测试必须逐张处理生产 Markdown 中的全部图。

- [ ] **Step 5: 在真实页面完成桌面与手机视觉检查**

启动本地后端与前端，使用认证后的浏览器依次打开五篇文章。桌面视口检查分层背景、语义色、文字、连线、循环与异常分支；手机视口检查每张图无需不可辨认缩放即可阅读。记录每篇文章的截图证据。任何图发生截断、重叠、颜色含义冲突或源码回退时，返回对应文章任务修复并重跑 Step 3–5。

- [ ] **Step 6: 请求独立代码与内容复审**

使用 `requesting-code-review`，要求审查者按设计文档检查：图示事实边界、跨文章配色一致性、手机可读性、测试是否真实调用 Mermaid、文章更新时间和旧标记防护。Critical 或 Important 问题清零后才进入发布。

- [ ] **Step 7: 提交交叉审校修复**

```bash
git add backend/app/ai_notes/README.md backend/app/ai_notes/content backend/tests/test_ai_notes_production_content.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs: finalize AI notes diagram quality"
```

如果 Step 1–6 没有产生新改动，则跳过空提交，并记录最后一个内容提交 SHA。

- [ ] **Step 8: 合并、推送并执行受控生产部署**

从干净、已复审的发布提交快进合并回 `master`，确认未跟踪的用户文件未被加入提交，推送 `origin/master`，然后运行：

```bash
./deploy/cloud/deploy.sh "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Expected: 输出 `CLOUD_PLATFORM_DEPLOY_OK release=<master SHA> mode=dingtalk`。

- [ ] **Step 9: 做生产后独立核验**

使用受保护部署配置连接生产主机，确认 `/opt/orbbec-agent-platform/current` 指向推送的 master SHA；五个核心容器健康；运行中 `platform-api` 容器执行 `python -m app.ai_notes.validate` 输出 5 categories / 5 published articles。公网 `https://agent.orbbec.com.cn/api/health` 返回 200；认证后打开五篇文章并复核桌面与手机图示。

- [ ] **Step 10: 转入第二批文章计划**

第一批视觉质量更新上线稳定后，使用已批准设计 `docs/superpowers/specs/2026-08-28-ai-notes-production-systems-second-batch-design.md` 编写独立的第二批实施计划，从《LLM 应用系统架构：从一次请求到可靠回答》开始，保持逐篇精读、评审和上线。
