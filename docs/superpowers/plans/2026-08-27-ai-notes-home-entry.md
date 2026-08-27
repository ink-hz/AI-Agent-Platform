# AI 工程笔记首页入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从全局产品导航移除 `AI 工程笔记`，并在 Agent 大脑首页说明文字下方增加一个跳转到现有 `/ai-notes` 阅读工作区的轻量链接。

**Architecture:** 保留现有 AI 工程笔记路由、API 和阅读工作区，仅调整发现入口。`AppShell` 不再把笔记当作全局产品区；`BrainPage` 使用带真实 `href` 的语义化链接，并通过可注入导航回调接入 `BrainWorkspacePage` 现有的客户端导航。

**Tech Stack:** React 19、TypeScript、现有客户端路由、Vitest、jsdom、CSS。

## Global Constraints

- 顶部产品导航不得出现 `AI 工程笔记`。
- 首页入口文案固定为 `AI 工程笔记 →`，位置固定在 Agent 大脑说明文字之后、任务输入框之前。
- 入口只出现在 `/` 的新对话首页，不出现在具体对话或其他页面。
- 入口必须保留真实 `/ai-notes` `href`、部署前缀、修饰键点击和键盘可访问性。
- 不增加副标题、宣传语、图标、大卡片、推荐文章、未读数或内容预取。
- `/ai-notes` 路由、全高阅读布局、左侧分类树、认证和内容 API 均保持不变。
- 当前内容仍为 5 个空分类、0 篇文章；本计划不迁移文章。
- 不修改或删除现有未跟踪文件，不推送远端，不执行生产部署。

---

## File map

- `webui/src/AppShell.tsx`：从全局产品导航删除 AI 工程笔记项，保留阅读工作区 section 判断。
- `webui/src/AppShell.brain.test.tsx`：锁定所有角色的顶部导航和笔记工作区壳层行为。
- `webui/src/pages/BrainPage.tsx`：在首页 Hero 中渲染轻量语义化入口。
- `webui/src/pages/BrainPage.test.tsx`：验证入口位置、真实 href 和普通点击导航。
- `webui/src/pages/BrainWorkspacePage.tsx`：把现有 `onNavigate` 注入首页笔记入口。
- `webui/src/pages/BrainWorkspacePage.test.tsx`：验证入口只在新对话首页出现。
- `webui/src/styles.css`：提供克制的链接、hover 和键盘焦点样式。
- `webui/src/styles.test.ts`：锁定入口的低权重视觉和焦点状态。

### Task 1: 从全局产品导航移除笔记入口

**Files:**
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/AppShell.brain.test.tsx`

**Interfaces:**
- Consumes: `routeSection(route) -> RouteSection`
- Produces: `AppShell` 顶部导航只包含 Agent 大脑、专业 Agent 和按角色显示的管理中心；`ai-notes` section 仍控制工作区 CSS class。

- [ ] **Step 1: 修改测试，声明 AI 工程笔记不属于全局导航**

在 `AppShell.brain.test.tsx` 中把成员导航精确断言改为：

```tsx
expect(container.querySelector(".product-nav")?.textContent).toBe("Agent 大脑专业 Agent");
```

把 owner 测试中的笔记断言改为：

```tsx
expect(navigation).not.toContain("AI 工程笔记");
```

把原来的 `marks AI notes as the current top-level entry` 测试改为：

```tsx
it("keeps the AI notes workspace without a global navigation entry", async () => {
  await act(async () => root.render(
    <AppShell route={{ name: "ai-notes" }} account={member}><p>文章</p></AppShell>,
  ));
  expect(container.querySelector('.product-nav a[href="/ai-notes"]')).toBeNull();
  expect(container.querySelector("main.page.is-ai-notes-workspace")).not.toBeNull();
  expect(container.querySelector(".app.is-ai-notes-workspace-shell")).not.toBeNull();
  expect(container.querySelector("footer.site-foot")).toBeNull();
});
```

- [ ] **Step 2: 运行测试并确认旧导航使其失败**

Run: `cd webui && npm test -- --run src/AppShell.brain.test.tsx`

Expected: FAIL，导航文本仍包含 `AI 工程笔记`，且 `/ai-notes` 顶部链接仍存在。

- [ ] **Step 3: 删除全局导航项但保留 section 和工作区判断**

在 `AppShell.tsx` 中把 `USE_NAVIGATION` 改为：

```tsx
const USE_NAVIGATION = [
  { label: "Agent 大脑", path: "/", section: "brain" },
  { label: "专业 Agent", path: "/agents", section: "agents" },
] as const;
```

不要删除 `NavigationItem.section` 中的 `"ai-notes"`，也不要改动：

```tsx
const aiNotesWorkspace = route.name === "ai-notes" || route.name === "ai-note";
```

- [ ] **Step 4: 运行 AppShell 测试**

Run: `cd webui && npm test -- --run src/AppShell.brain.test.tsx`

Expected: PASS，6 个测试全部通过。

- [ ] **Step 5: 提交导航层级调整**

```bash
git add webui/src/AppShell.tsx webui/src/AppShell.brain.test.tsx
git commit -m "refactor: remove AI notes from global navigation"
```

### Task 2: 在 Agent 大脑首页增加轻量入口

**Files:**
- Modify: `webui/src/pages/BrainPage.tsx`
- Modify: `webui/src/pages/BrainPage.test.tsx`
- Modify: `webui/src/pages/BrainWorkspacePage.tsx`
- Modify: `webui/src/pages/BrainWorkspacePage.test.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes: `platformPath(path: string) -> string` 和 `BrainWorkspacePage.onNavigate(path: string) -> void`
- Produces: `BrainPage.onOpenAiNotes?: (path: string) -> void`；`.brain-ai-notes-entry` 语义化首页链接。

- [ ] **Step 1: 写首页入口的失败测试**

在 `BrainPage.test.tsx` 增加：

```tsx
it("opens AI engineering notes from one quiet home entry", async () => {
  const onOpenAiNotes = vi.fn();
  await act(async () => root.render(
    <BrainPage
      account={account}
      client={{ createSubmission: vi.fn() }}
      onOpenAiNotes={onOpenAiNotes}
    />,
  ));

  const entry = container.querySelector<HTMLAnchorElement>(".brain-ai-notes-entry");
  expect(entry?.textContent).toBe("AI 工程笔记 →");
  expect(entry?.getAttribute("href")).toBe("/ai-notes");
  expect(container.querySelector(".brain-hero > span + .brain-ai-notes-entry")).toBe(entry);
  expect(entry?.nextElementSibling?.classList.contains("brain-composer")).toBe(true);

  await act(async () => entry?.click());
  expect(onOpenAiNotes).toHaveBeenCalledWith("/ai-notes");
});
```

在 `BrainWorkspacePage.test.tsx` 的首页测试增加：

```tsx
expect(container.querySelector('.brain-ai-notes-entry[href="/ai-notes"]')).not.toBeNull();
```

在具体 Session 测试增加：

```tsx
expect(container.querySelector(".brain-ai-notes-entry")).toBeNull();
```

在 `styles.test.ts` 增加精确选择器断言：

```tsx
expect(rule(".brain-ai-notes-entry")).toContain("display: inline-flex");
expect(rule(".brain-ai-notes-entry")).toContain("min-height: 36px");
expect(rule(".brain-ai-notes-entry:focus-visible")).toContain("outline: 3px solid");
```

- [ ] **Step 2: 运行聚焦测试并确认入口缺失**

Run: `cd webui && npm test -- --run src/pages/BrainPage.test.tsx src/pages/BrainWorkspacePage.test.tsx src/styles.test.ts`

Expected: FAIL，`BrainPage` 尚无 `onOpenAiNotes` 属性和 `.brain-ai-notes-entry`。

- [ ] **Step 3: 实现语义化入口和可注入导航**

在 `BrainPage.tsx`：

```tsx
import { platformPath, type Account } from "../auth";
```

给 props 增加：

```tsx
onOpenAiNotes = (path) => navigate(path),
```

及类型：

```tsx
onOpenAiNotes?: (path: string) => void;
```

在说明 `<span>` 和 `<form>` 之间加入：

```tsx
<a
  className="brain-ai-notes-entry"
  href={platformPath("/ai-notes")}
  onClick={(event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpenAiNotes("/ai-notes");
  }}
>AI 工程笔记 <span aria-hidden="true">→</span></a>
```

在 `BrainWorkspacePage.tsx` 的 `BrainPage` 调用中加入：

```tsx
onOpenAiNotes={onNavigate}
```

- [ ] **Step 4: 实现克制视觉和键盘焦点**

在 `styles.css` 的 Brain Hero 规则旁增加：

```css
.brain-ai-notes-entry { display: inline-flex; min-height: 36px; align-items: center; gap: 6px; margin-top: 17px; padding: 6px 9px; border-radius: 8px; color: var(--brand); font-size: 13px; font-weight: 750; text-decoration: none; }
.brain-ai-notes-entry:hover { background: var(--brand-wash); }
.brain-ai-notes-entry:focus-visible { outline: 3px solid rgba(36,104,197,.34); outline-offset: 2px; }
.brain-ai-notes-entry + .brain-composer { margin-top: 20px; }
```

不要增加图标、卡片容器、阴影、渐变或说明文字。

- [ ] **Step 5: 运行入口与样式测试**

Run: `cd webui && npm test -- --run src/pages/BrainPage.test.tsx src/pages/BrainWorkspacePage.test.tsx src/styles.test.ts`

Expected: PASS，BrainPage 7 个测试、BrainWorkspacePage 4 个测试及样式测试全部通过。

- [ ] **Step 6: 提交首页入口**

```bash
git add webui/src/pages/BrainPage.tsx webui/src/pages/BrainPage.test.tsx webui/src/pages/BrainWorkspacePage.tsx webui/src/pages/BrainWorkspacePage.test.tsx webui/src/styles.css webui/src/styles.test.ts
git commit -m "feat: link AI notes from brain home"
```

### Task 3: 回归验收

**Files:**
- Review: `webui/src/AppShell.tsx`
- Review: `webui/src/pages/BrainPage.tsx`
- Review: `webui/src/pages/BrainWorkspacePage.tsx`
- Review: `webui/src/pages/AiNotesPage.tsx`

**Interfaces:**
- Consumes: Tasks 1–2 的最终实现。
- Produces: 可合并的前端入口调整，不包含内容迁移或部署。

- [ ] **Step 1: 运行 AI 笔记与导航定向回归**

Run:

```bash
cd webui && npm test -- --run \
  src/AppShell.brain.test.tsx \
  src/pages/BrainPage.test.tsx \
  src/pages/BrainWorkspacePage.test.tsx \
  src/pages/AiNotesPage.test.tsx \
  src/router.test.ts \
  src/auth.test.ts \
  src/styles.test.ts
```

Expected: 所有选定测试通过；顶部导航无 AI 工程笔记，首页有唯一入口，阅读页行为不变。

- [ ] **Step 2: 运行前端全量测试和生产构建**

Run: `cd webui && npm test -- --run && npm run build`

Expected: 全量测试通过；TypeScript 与 Vite 生产构建退出码为 0。允许保留现有的大 chunk 提示，但不得新增构建错误。

- [ ] **Step 3: 核对范围与工作区**

Run:

```bash
git diff --check
git diff --name-only HEAD~2..HEAD
git status --short
```

Expected: 只包含计划列出的前端代码、测试和样式；既有未跟踪文件保持原样。

- [ ] **Step 4: 独立代码审查**

审查以下验收点：

- `AI 工程笔记` 不再出现在顶部产品导航；
- 首页入口在说明文字和输入框之间，只有固定短文案；
- 普通点击走客户端导航，修饰键和真实 href 保留浏览器行为；
- 对话详情没有入口；
- `/ai-notes` 全高工作区和移动目录不受影响；
- 没有文章迁移、后端改动或无关重构。

- [ ] **Step 5: 不创建额外提交，报告验证结果**

报告定向测试数、全量测试数、构建结果、审查结果、当前内容数（5 个分类、0 篇文章）以及未跟踪文件保持情况。不要声称已推送或上线。
