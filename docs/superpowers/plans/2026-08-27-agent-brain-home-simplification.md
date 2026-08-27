# Agent 大脑首页简化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agent 大脑新对话首页改成以输入框为唯一主操作的简洁页面，并把 `AI 工程笔记 →` 移到主内容区右上角。

**Architecture:** 只调整 `BrainPage` 的展示结构和 Agent 大脑首页样式，不改变 Conversation 创建、错误处理、路由或权限。新 DOM 分成右上角工具区和中央输入区；`BrainWorkspacePage` 继续决定入口只出现在新对话首页。

**Tech Stack:** React 19、TypeScript、CSS、Vitest、jsdom、Vite

## Global Constraints

- 保留左侧会话栏、Conversation 提交和重试数据流。
- 入口文案固定为 `AI 工程笔记 →`，目标固定为 `/ai-notes`，继续支持部署前缀、修饰键和键盘访问。
- 删除眉题、能力说明、首版限制说明和三张示例卡片。
- 不增加欢迎语、宣传语、推荐任务、卡片、悬浮按钮或新依赖。
- 入口只出现在新对话首页；具体对话页不显示。
- 不修改 AI 工程笔记阅读页、后端 API、权限或文章内容。
- 保留当前未跟踪文件，不将其加入提交。

---

### Task 1: 收敛首页内容结构

**Files:**
- Modify: `webui/src/pages/BrainPage.test.tsx`
- Modify: `webui/src/pages/BrainPage.tsx`

**Interfaces:**
- Consumes: `BrainPage` 现有 `account`、`client`、`onOpenAiNotes`、`onConversationCreated` 和 `onOpenConversation` props。
- Produces: `.brain-home-toolbar`、`.brain-home-focus`、`.brain-ai-notes-entry`、`.brain-composer` DOM；所有提交与错误行为保持原接口。

- [ ] **Step 1: 写失败测试，固定精简后的首页内容**

把 `is immediately usable...` 用例中的示例断言替换为删除内容断言，并在笔记入口用例中固定新容器：

```tsx
expect(container.querySelector("h1")?.textContent).toBe("Agent 大脑");
expect(container.querySelector("textarea")?.disabled).toBe(false);
expect(container.querySelectorAll(".brain-example")).toHaveLength(0);
expect(container.textContent).not.toContain("把原始需求直接交给它");
expect(container.textContent).not.toContain("它会判断是否需要专业 Agent");
expect(container.textContent).not.toContain("首版支持纯文本任务");
```

在笔记入口用例中使用：

```tsx
expect(entry?.parentElement?.classList.contains("brain-home-toolbar")).toBe(true);
expect(container.querySelector(".brain-home-focus h1")?.textContent).toBe("Agent 大脑");
expect(container.querySelector(".brain-home-focus .brain-composer")).not.toBeNull();
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd webui && npm test -- --run src/pages/BrainPage.test.tsx
```

Expected: FAIL，因为旧页面仍有眉题、说明、限制文字和 3 个 `.brain-example`，且没有 `.brain-home-toolbar` / `.brain-home-focus`。

- [ ] **Step 3: 实现最小 JSX 调整**

删除 `EXAMPLES` 常量。把 `BrainPage` 的返回结构改为：

```tsx
return <div className="brain-page">
  <section className="brain-hero" aria-labelledby="brain-heading">
    <div className="brain-home-toolbar">
      <a
        className="brain-ai-notes-entry"
        href={platformPath("/ai-notes")}
        onClick={(event) => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onOpenAiNotes("/ai-notes");
        }}
      >AI 工程笔记 <span aria-hidden="true">→</span></a>
    </div>
    <div className="brain-home-focus">
      <h1 id="brain-heading">Agent 大脑</h1>
      <form className="brain-composer" onSubmit={submit}>
        <label htmlFor="brain-request">你想完成什么？</label>
        <textarea
          autoFocus
          disabled={account.hard_stale_read_only}
          id="brain-request"
          aria-label="你想完成什么？"
          maxLength={32 * 1024}
          onChange={(event) => {
            const next = event.target.value;
            setText(next);
            if (retained.current?.text !== next.trim()) retained.current = null;
            setFailure(null);
          }}
          placeholder="描述目标、背景和希望得到的结果…"
          rows={5}
          value={text}
        />
        <div className="brain-composer-actions">
          <button className="brain-submit" disabled={!text.trim() || inputTooLarge || pending || account.hard_stale_read_only} type="submit">
            {pending ? "正在创建…" : "开始对话"}
          </button>
        </div>
      </form>
      {inputTooLarge && <p className="mission-input-error" role="alert">输入超过 32 KiB，请精简后再提交。</p>}
      {failure && <div className="brain-submit-error" role="alert">
        <span>{failure === "unavailable"
          ? "Agent 大脑暂不可用。请稍后使用同一次请求重试。"
          : "对话暂未创建成功。网络恢复后可使用同一次请求安全重试。"}</span>
        <button className="brain-retry" disabled={pending} onClick={() => void send()} type="button">重新提交</button>
      </div>}
    </div>
  </section>
</div>;
```

- [ ] **Step 4: 运行行为测试并确认 GREEN**

Run:

```bash
cd webui && npm test -- --run src/pages/BrainPage.test.tsx src/pages/BrainWorkspacePage.test.tsx
```

Expected: 2 个测试文件全部通过；提交、重试、超限、卸载中止和具体对话页不显示入口的测试继续通过。

- [ ] **Step 5: 提交结构调整**

```bash
git add webui/src/pages/BrainPage.tsx webui/src/pages/BrainPage.test.tsx
git commit -m "refactor: simplify Agent Brain home content"
```

---

### Task 2: 建立右上角入口和紧凑输入布局

**Files:**
- Modify: `webui/src/styles.test.ts`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: Task 1 产生的 `.brain-home-toolbar`、`.brain-home-focus`、`.brain-ai-notes-entry` 和 `.brain-composer`。
- Produces: 桌面端右上角工具区、紧凑中央输入区，以及窄屏下“入口、标题、输入框”的自然排列。

- [ ] **Step 1: 写失败样式测试**

把现有 AI 工程笔记入口断言扩展为：

```ts
expect(rule(".brain-home-toolbar")).toContain("display: flex");
expect(rule(".brain-home-toolbar")).toContain("justify-content: flex-end");
expect(rule(".brain-home-focus")).toContain("width: min(760px, 100%)");
expect(rule(".brain-home-focus")).toContain("margin:");
expect(rule(".brain-ai-notes-entry")).not.toContain("position: fixed");
expect(rule(".brain-home-focus > h1")).toContain("font-size: clamp(32px, 5vw, 44px)");
expect(rule(".brain-composer-actions")).toContain("justify-content: flex-end");
```

在最后一个 `@media (max-width: 720px)` 断言中增加：

```ts
expect(mobile).toContain(".brain-home-toolbar");
expect(mobile).toContain(".brain-home-focus");
```

- [ ] **Step 2: 运行样式测试并确认 RED**

Run:

```bash
cd webui && npm test -- --run src/styles.test.ts
```

Expected: FAIL，因为新布局选择器和紧凑标题规则尚不存在。

- [ ] **Step 3: 实现桌面与窄屏样式**

用以下首页样式替换旧 Hero、说明、入口相邻选择器和示例卡片规则：

```css
.brain-hero { padding: 22px 0 30px; }
.brain-home-toolbar { display: flex; min-height: 36px; align-items: center; justify-content: flex-end; }
.brain-home-focus { width: min(760px, 100%); margin: clamp(28px, 9vh, 86px) auto 0; text-align: center; }
.brain-home-focus > h1 { margin: 0; font-size: clamp(32px, 5vw, 44px); letter-spacing: -.045em; line-height: 1.08; }
.brain-ai-notes-entry { display: inline-flex; min-height: 36px; align-items: center; gap: 6px; padding: 6px 9px; border-radius: 8px; color: var(--brand); font-size: 13px; font-weight: 750; text-decoration: none; }
.brain-ai-notes-entry:hover { background: var(--brand-wash); }
.brain-ai-notes-entry:focus-visible { outline: 3px solid rgba(36,104,197,.34); outline-offset: 2px; }
.brain-home-focus .brain-composer { margin-top: 24px; }
.brain-composer-actions { justify-content: flex-end; }
```

在最后一个 `@media (max-width: 720px)` 中使用：

```css
.brain-hero { padding-top: 14px; }
.brain-home-toolbar { min-height: 42px; }
.brain-home-focus { margin-top: clamp(20px, 7vh, 52px); }
.brain-home-focus > h1 { font-size: 34px; }
```

删除已经没有消费者的 `.brain-hero > p`、`.brain-hero > span`、`.brain-ai-notes-entry + .brain-composer`、`.brain-examples` 和 `.brain-example` 首页规则；不要删除仍被其他页面共享的 `.use-page-intro > p` 或 `.agent-use-grid` 规则。

- [ ] **Step 4: 运行定向测试并确认 GREEN**

Run:

```bash
cd webui && npm test -- --run src/styles.test.ts src/pages/BrainPage.test.tsx src/pages/BrainWorkspacePage.test.tsx
```

Expected: 3 个测试文件全部通过。

- [ ] **Step 5: 提交视觉调整**

```bash
git add webui/src/styles.css webui/src/styles.test.ts
git commit -m "style: focus Agent Brain home on the composer"
```

---

### Task 3: 完整验证与交付检查

**Files:**
- Verify only; no product files change.

**Interfaces:**
- Consumes: Tasks 1–2 的页面结构和样式。
- Produces: 可审查、可构建、可部署的前端提交。

- [ ] **Step 1: 运行前端全量测试**

Run:

```bash
cd webui && npm test -- --run
```

Expected: 58 个测试文件、至少 454 项测试通过，0 项失败。

- [ ] **Step 2: 运行生产构建和静态检查**

Run:

```bash
cd webui && npm run build
cd .. && git diff --check HEAD~2..HEAD
```

Expected: TypeScript 与 Vite 构建成功，`git diff --check` 无输出。

- [ ] **Step 3: 核对范围和工作区状态**

Run:

```bash
git diff --name-only HEAD~2..HEAD
git status --short
```

Expected: 产品改动只包含 `BrainPage.tsx`、`BrainPage.test.tsx`、`styles.css` 和 `styles.test.ts`；用户现有未跟踪文件保持未跟踪且未进入提交。

- [ ] **Step 4: 请求代码审查并修复 Critical / Important 问题**

审查重点：删除内容是否准确、笔记链接原生行为是否保留、错误/重试是否仍在中央输入区、窄屏入口是否不遮挡移动会话按钮、是否误改共享样式。

- [ ] **Step 5: 报告结果后进入独立的文章迁移设计**

报告定向测试、全量测试、构建、审查和提交状态。不要把文章盘点、清洗或迁移混入本计划的产品提交。
