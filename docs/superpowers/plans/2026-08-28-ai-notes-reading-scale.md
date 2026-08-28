# AI 工程笔记阅读尺度优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩大 AI 工程笔记正文的桌面阅读宽度和桌面/手机字号，消除大屏中过量留白并提升持续阅读舒适度。

**Architecture:** 只修改 AI notes 专属 CSS 和现有样式合同，不改变组件、文章数据或页面结构。通过 `.ai-note-article` 控制画布，通过 `.article-markdown` 及其标题、代码、表格规则控制排版；720px 移动断点继续覆盖字号和内边距。

**Tech Stack:** CSS、Vitest 2.1、现有 CSS 文本合同测试。

## Global Constraints

- 桌面文章容器最大宽度为 `1040px`，水平内边距为 `48px`。
- 桌面正文为 `17px`、行高 `1.78`；H2/H3/H4 为 `25px/20px/17px`。
- 正文代码和表格为 `14px`。
- 720px 及以下正文为 `16px`，文章水平内边距为 `18px`。
- `.ai-notes-reader-notice` 与文章容器使用相同 `1040px` 最大宽度。
- 不修改左侧目录树、导航、Markdown/Mermaid 组件、文章内容或其他页面样式。
- 保留现有打印规则。

---

### Task 1: 用样式合同驱动阅读尺度修改

**Files:**
- Modify: `webui/src/styles.test.ts`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `rule(selector)` 和 `lastBlock(mediaQuery)` CSS 测试帮助函数。
- Produces: AI notes 桌面和手机阅读尺度的回归合同。

- [ ] **Step 1: 写失败样式合同**

在 `gives AI notes an independent two-column reading workspace` 测试中替换旧宽度断言并加入：

```tsx
expect(rule(".ai-notes-reader-notice")).toContain("max-width: 1040px");
expect(rule(".ai-note-article")).toContain("max-width: 1040px");
expect(rule(".ai-note-article")).toContain("padding: 48px 48px 80px");
expect(rule(".article-markdown")).toContain("font-size: 17px");
expect(rule(".article-markdown")).toContain("line-height: 1.78");
expect(rule(".article-markdown h2")).toContain("font-size: 25px");
expect(rule(".article-markdown h3")).toContain("font-size: 20px");
expect(rule(".article-markdown h4")).toContain("font-size: 17px");
expect(rule(".article-markdown code")).toContain("font-size: 14px");
expect(rule(".article-markdown table")).toContain("font-size: 14px");

const mobile = lastBlock("@media (max-width: 720px)");
expect(mobile).toContain(".ai-note-article { padding: 38px 18px 72px; }");
expect(mobile).toContain(".article-markdown { font-size: 16px; }");
```

- [ ] **Step 2: 运行 RED 测试**

Run:

```bash
cd webui
npm test -- --run src/styles.test.ts
```

Expected: FAIL，输出仍包含旧值 `820px`、`15px` 和 `14px`。

- [ ] **Step 3: 实现最小 CSS 修改**

在 `webui/src/styles.css` 中改为：

```css
.ai-notes-reader-notice { max-width: 1040px; }
.ai-note-article { max-width: 1040px; margin: 0 auto; padding: 48px 48px 80px; }
.article-markdown { min-width: 0; padding-top: 30px; color: var(--ink); font-size: 17px; line-height: 1.78; overflow-wrap: anywhere; }
.article-markdown h2 { padding-bottom: 8px; border-bottom: 1px solid var(--line-soft); font-size: 25px; }
.article-markdown h3 { font-size: 20px; }
.article-markdown h4 { font-size: 17px; }
.article-markdown code { border-radius: 4px; background: #edf2f7; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 14px; }
.article-markdown table { width: 100%; min-width: 520px; border-collapse: collapse; font-size: 14px; }

@media (max-width: 720px) {
  .ai-note-article { padding: 38px 18px 72px; }
  .article-markdown { font-size: 16px; }
}
```

- [ ] **Step 4: 运行 GREEN 测试和 AI notes 定向回归**

```bash
cd webui
npm test -- --run src/styles.test.ts src/pages/AiNotesPage.test.tsx src/components/ai-notes/ArticleMarkdown.test.tsx src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交实现**

```bash
git add webui/src/styles.css webui/src/styles.test.ts
git commit -m "style: improve AI notes reading scale"
```

---

### Task 2: 独立复审、全量验证和生产发布

**Files:** Modify only if review identifies a concrete defect.

- [ ] **Step 1: 请求独立复审**

使用 `requesting-code-review`，检查改动是否只作用于 AI notes、桌面与手机合同是否完整、宽度和字号是否形成合理行长、打印规则是否保留。Critical/Important 清零后继续。

- [ ] **Step 2: 运行完整前端验证**

```bash
cd webui
npm test -- --run
npm run build
cd ..
git diff --check
```

Expected: 全量 Vitest 和生产构建 PASS；只有既有的大 chunk 警告。

- [ ] **Step 3: 合并与推送**

从干净的隔离工作树快进合并到 `master`，确认用户未跟踪文件没有进入提交，推送 `origin/master`。

- [ ] **Step 4: 受控生产部署**

从干净工作树运行：

```bash
./deploy/cloud/deploy.sh "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Expected: `CLOUD_PLATFORM_DEPLOY_OK release=<master SHA> mode=dingtalk`。

- [ ] **Step 5: 生产后核验**

确认 production current 指向 release；核心容器 healthy；公网 `/api/health` 返回 200。让用户用相同大屏视口刷新文章页，确认正文宽度、字号和留白改善。
