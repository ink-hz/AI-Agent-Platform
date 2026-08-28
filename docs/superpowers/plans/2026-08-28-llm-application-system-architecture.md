# 《LLM 应用系统架构》单篇迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` task by task. Use `test-driven-development` for content/rendering contracts, `requesting-code-review` before merge, and `verification-before-completion` before release.

**Goal:** 精读两篇旧源稿，核验并重新创作《LLM 应用系统架构：从一次请求到可靠回答》，作为第二批“生产级 AI 系统”专题的第一篇文章，独立评审、独立上线。

**Architecture:** 文章只讲 LLM 应用层从请求到可靠回答的边界：入口、上下文、编排、RAG、工具、模型访问、输出验证、证据与反馈。推理服务内部、云原生运行、可观测性实现和身份治理只标出接口，细节留给后续专篇。沿用现有 `Markdown -> AiNotesRepository -> API -> ArticleMarkdown -> MermaidDiagram` 链路，不改变首页、左树、API 或阅读布局。

**Tech Stack:** Markdown frontmatter、Mermaid 11.17.2、Python 3.11、pytest、React 19、Vitest 2.1、DOMPurify 3.4。

## Quality gates

- `/Users/neo/Developer/personal/starship-blog-source` 只读，不加入构建、运行或部署依赖。
- 必须从头到尾精读两个源文件；关键词扫描只能在精读后查漏，不能代替语义判断。
- 旧稿仅是材料。产品能力、协议、安全和性能结论使用迁移当天的一手资料核验。
- 以新问题结构重写；不恢复旧组织、旧项目、旧日期、版本示例、营销数字或预测。
- 作者 `苍渊`，座右铭 `博观而约取，厚积而薄发。`；首次发布日期使用实际上线日。
- 先写 `draft: true`；正文、图示、来源、交叉复读和真实渲染通过后才改为 `false`。
- 全景图用浅色分区，流程图用固定语义色；手机不可读的长横图改为 TB 或拆图。
- 质量不是字数或图数。与首批文章或后续专篇重复的章节必须删除或只保留接口说明。

## Files

Read only:

- `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构深度指南.md`
- `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构理论指南.md`
- 当前五篇 `backend/app/ai_notes/content/*/*.md` 生产文章

Create:

- `backend/app/ai_notes/content/01-foundations/02-llm-application-system-architecture.md`

Modify:

- `backend/tests/test_ai_notes_production_content.py`
- `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

## Boundary

This article owns:

1. 请求进入系统后，确定性组件和概率性组件如何协作；
2. 上下文、检索、工具和模型如何被编排，而非简单串成 `Prompt -> LLM`；
3. “有输出”到“可交付回答”之间的验证、证据与失败处理；
4. 何时选择简单调用、RAG、工具型 Agent 或异步任务；
5. 如何把可靠性写成可验证的系统契约。

This article does not own:

- Prefill/Decode、连续批处理、KV Cache、量化、投机解码和 GPU 容量；留给《LLM 推理服务工程》。
- Kubernetes 调度、弹性、模型制品、灰度和节点故障；留给《AI × 云原生》。
- Trace、Token 成本、评估平台和告警实现；留给《LLM / Agent 可观测性》。
- 工作负载身份、Token Exchange、最小权限和审批协议；留给《Agent 身份与最小权限》。
- ReAct、多 Agent、RAG 算法和 Claude Code 的重复教程；只链接首批文章。

## Planned visuals

1. **应用系统分层（TB）**：入口与请求、上下文与编排、知识与工具、模型访问、验证与证据五个浅色分区。
2. **从请求到可靠回答（TB）**：请求归一化 -> 身份/策略 -> 路由 -> 上下文 -> 检索/工具 -> 生成 -> 结构/引用/业务校验 -> 返回或修复/降级。
3. **能力选择边界（TB）**：根据外部知识、副作用和运行时长分流到简单调用、RAG、工具调用或异步任务；Agent 不是默认答案。

模型网关和运行时用 `infra`，模型推理用 `model`，控制门禁用 `policy`，失败分支用 `risk`，证据用 `success`。图前说明观察问题，图后解释权衡。

---

### Task 1: 精读、去重并建立事实边界

- [ ] **Step 1: 固定只读源文件身份**

```bash
shasum -a 256 \
  /Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构深度指南.md \
  /Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构理论指南.md
```

记录哈希和总行数。文件在迁移中变化时，从头重读新版本，不混合笔记。

- [ ] **Step 2: 从头到尾精读全部约 4,000 行**

逐节记录：可复用命题、与首批重复、应留给后续专篇、过时事实、无来源数字、旧组织痕迹，以及值得重画的关系。禁止先用关键词把段落自动归类后直接生成正文。

- [ ] **Step 3: 交叉复读五篇线上文章**

- Agent 循环、状态和信任决策链接企业 Agent 文章；
- RAG 只保留应用层接口与失败影响，算法链接 RAG 文章；
- Claude Code 只作为工具型 Agent 示例；
- AI Native 只引用人机责任；
- 学习地图只作专题导航。

- [ ] **Step 4: 建立事实审计**

对保留的外部事实记录 `一手资料明确说明 / 工程抽象 / 不采用`。优先协议、标准、一手论文、官方产品文档和安全机构原始指南；二手博客只用于发现线索。

**Checkpoint:** 写正文前，能说明本篇讲什么、不讲什么、每个关键事实由什么支持。

---

### Task 2: 用 RED 合同锁定文章与图示

**Files:** Modify the two test files.

- [ ] **Step 1: 写后端失败测试**

增加实际第二批发布日期常量，并新增：

```python
def test_publishes_clean_llm_application_system_architecture_note() -> None:
    article = published_article("foundations", "llm-application-system-architecture")
    assert article.title == "LLM 应用系统架构：从一次请求到可靠回答"
    assert article.filename == "llm-application-system-architecture.md"
    assert article.author == "苍渊"
    assert article.motto == "博观而约取，厚积而薄发。"
    assert article.published_at == SECOND_BATCH_PUBLISHED_ON
    assert article.updated_at == SECOND_BATCH_PUBLISHED_ON
    assert article.tags == ("LLM", "系统架构", "AI 工程")
    assert_clean_body(article.markdown)


def test_llm_application_note_visualizes_reliable_answer_boundary() -> None:
    article = published_article("foundations", "llm-application-system-architecture")
    diagrams = mermaid_blocks(article.markdown)
    combined = "\n".join(diagrams)
    assert len(diagrams) == 3
    for label in (
        "应用系统分层", "从请求到可靠回答", "能力选择边界",
        "模型推理", "检索", "工具", "输出验证", "完成证据",
    ):
        assert label in combined
    assert "flowchart LR" not in combined
    assert all("classDef" in diagram or "style" in diagram for diagram in diagrams)
```

把“首批恰好五篇”调整为同时锁定首批五篇和新增文章的目录顺序，不能删除首批保护。

- [ ] **Step 2: 写前端真实渲染失败测试**

```tsx
it("renders every LLM application architecture diagram", async () => {
  const sources = mermaidBlocks(productionArticle(
    "01-foundations/02-llm-application-system-architecture.md",
  ));
  expect(sources).toHaveLength(3);
  expect(sources.join("\n")).toContain("从请求到可靠回答");
  await expectProductionDiagramsToRender(sources);
});
```

- [ ] **Step 3: 运行并保存 RED 证据**

```bash
cd backend && .venv/bin/pytest -q tests/test_ai_notes_production_content.py
cd ../webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 因目标文件不存在失败。若意外通过，先调查同 slug 内容，不能覆盖未知文章。

---

### Task 3: 写完整但未发布的草稿

**Files:** Create the target Markdown.

- [ ] **Step 1: 创建 frontmatter**

```yaml
---
title: LLM 应用系统架构：从一次请求到可靠回答
slug: llm-application-system-architecture
description: 从应用层解释请求入口、上下文、RAG、工具、模型访问、输出验证与反馈如何共同形成可靠回答。
author: 苍渊
motto: 博观而约取，厚积而薄发。
publishedAt: <实际发布日期>
updatedAt: <实际发布日期>
tags:
  - LLM
  - 系统架构
  - AI 工程
draft: true
---
```

导语先澄清：模型调用成功不等于业务回答可靠；系统必须闭合输入、依据、行动、验证和责任。

- [ ] **Step 2: 按问题重写，不沿用旧稿目录**

建议主线：

1. 为什么 `Prompt -> Model -> Text` 不是生产架构；
2. 系统边界与确定性/概率性职责；
3. 请求入口、会话和上下文契约；
4. RAG、工具和模型访问的编排；
5. 输出结构、引用、业务规则与安全验证；
6. 超时、重试、幂等、降级和人工接管；
7. 简单调用、RAG、工具 Agent、异步任务的选择；
8. 可靠回答的验收证据；
9. 上线前检查清单与专题导航。

每节写清适用条件、权衡和失败方式；不迁入旧稿库代码、安装命令和产品列表。

- [ ] **Step 3: 加入三张计划图与相邻解释**

全部主要方向使用 `flowchart TB`。全景分区低饱和；拒绝、失败和降级分支使用 `risk`。运行时、网关、队列和存储不能标成 `model`。

- [ ] **Step 4: 添加一手来源与站内链接**

链接直接落到支撑具体断言的官方页面、标准或论文；不堆砌无正文对应的资料。首批文章使用站内相对链接。

- [ ] **Step 5: 完成草稿自审**

逐段判断事实/推断/建议；核对图文一致性、首批重复、后续专篇越界，以及删除图后正文是否完整。保持 `draft: true`。

---

### Task 4: 真实渲染、复读与发布开关

- [ ] **Step 1: 草稿期解析与逐图检查**

使用仓库解析器读取草稿，并用锁定的 Mermaid 11.17.2 + DOMPurify 链逐图渲染；草稿尚不进入生产索引，不能为提前变绿跳过 draft 阶段。

- [ ] **Step 2: 桌面与约 360px 手机视觉验收**

检查文字、分区、箭头标签、异常分支和图文顺序。任何整体缩到不可读的图都拆分或改 TB，不依赖无限横向滚动。

- [ ] **Step 3: 最后一次事实与边界复读**

重新打开全部一手来源，并从头读目标稿和五篇线上文章；删除重复、过度断言和没有直接来源的产品事实。

- [ ] **Step 4: 打开发行开关并跑 GREEN**

仅当前三步通过后，把 `draft` 改为 `false`，填实际 `publishedAt/updatedAt`。Expected: 目录为 5 分类、6 篇文章；后端合同与真实 Mermaid 测试全部 PASS。

- [ ] **Step 5: 单篇提交**

```bash
git add \
  backend/app/ai_notes/content/01-foundations/02-llm-application-system-architecture.md \
  backend/tests/test_ai_notes_production_content.py \
  webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs: publish LLM application system architecture note"
```

---

### Task 5: 独立复审、全量验证与上线

- [ ] **Step 1: 扫描危险内容**

```bash
! rg -n -i 'inkbot|starship|星舰|last reviewed|最后更新|真实项目|2800|19 轮|18 轮|3 天|2026-0[1-4]-|2025-01-21' backend/app/ai_notes/content --glob '*.md' --glob '!_index.md'
! rg -n '^# |<[A-Za-z][^>]*>' backend/app/ai_notes/content --glob '*.md' --glob '!_index.md'
git diff --check
```

- [ ] **Step 2: 独立内容与代码复审**

使用 `requesting-code-review` 检查：是否真正重写、一手来源、与首批和后续专篇的边界、颜色语义、360px 风险、三张图的必要性、日期/作者/目录和测试真实性。Critical/Important 清零才继续。

- [ ] **Step 3: 完整验证**

```bash
cd backend
.venv/bin/python -m app.ai_notes.validate
.venv/bin/pytest -q tests/test_ai_notes_api.py tests/test_ai_notes_production_content.py tests/test_ai_notes_repository.py tests/test_ai_notes_validation.py

cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx src/components/ai-notes/ArticleMarkdown.test.tsx src/pages/AiNotesPage.test.tsx
npm test -- --run
npm run build
```

- [ ] **Step 4: 合并、推送和受控部署**

从干净、已审查提交快进合并到 `master`，保持用户未跟踪文件不进入提交，推送 `origin/master`，再从干净工作树运行：

```bash
./deploy/cloud/deploy.sh "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Expected: `CLOUD_PLATFORM_DEPLOY_OK release=<master SHA> mode=dingtalk`。

- [ ] **Step 5: 生产后独立核验**

确认 current 指向 release；6 个核心容器 healthy；API 容器内内容校验为 5 分类、6 篇文章；公网健康检查 200；认证后桌面和手机复核新文章三张图。

- [ ] **Step 6: 决定下一篇**

本篇上线稳定后，再为《Agent 身份与最小权限》或《LLM 推理服务工程》建立下一份单篇计划；不并行迁移五篇，不以数量代替质量。
