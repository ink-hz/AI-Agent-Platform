# AI 工程笔记独立阅读页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让工程笔记在已有地址以独立阅读页打开，同时从平台入口进入时保留原工作页。

**Architecture:** `AppShell` 按笔记路由选独立框架；`AiNotesPage` 继续负责目录和正文。平台侧栏、AI 助手首页和公司全景用原生新页签链接指向已有笔记地址；全景状态不再包含笔记。后端路由、鉴权和部署入口保持现有契约。

**Tech Stack:** React 19、TypeScript、Vitest、FastAPI、Docker Compose。

## Global Constraints

- 不新增域名、Nginx 路由、数据库或笔记 API。
- 保留钉钉登录返回原文章及文章 API 鉴权。
- 生产只重建 `platform-api`；禁止在共享生产机执行独立配置的 `nginx -t -c` 或 `nginx -T -c`。

---

### Task 1: 独立笔记框架

**Files:** `webui/src/AppShell.tsx`, `webui/src/platform/platformShell.css`, `webui/src/styles.css`, `webui/src/AppShell.sidebar.test.tsx`, `webui/src/AppShell.brain.test.tsx`, `webui/src/styles.test.ts`。

**Interfaces:** 输入现有 `Route`、`Account` 与 `children`；笔记路由输出独立 header/main，其他路由输出原平台框架。

- [ ] **Step 1: Write the failing test.** 在 `AppShell.sidebar.test.tsx` 渲染 `{name:'ai-note',categorySlug:'foundations',articleSlug:'handbook'}`，断言无 `.platform-sidebar`，有笔记标题、`/brain` 返回链接及正文 `main`。
- [ ] **Step 2: Run it red.** `cd webui && ./node_modules/.bin/vitest run src/AppShell.sidebar.test.tsx`；预期因平台侧栏仍存在而失败。
- [ ] **Step 3: Implement.** `AppShell` 在所有 hooks 后对 `aiNotesWorkspace` 输出独立框架；CSS 让阅读区占满视口并保留现有移动目录。
- [ ] **Step 4: Run it green.** 重跑该文件；预期通过。
- [ ] **Step 5: Commit.** 提交框架代码与测试。

### Task 2: 原生新页签入口

**Files:** `webui/src/platform/PlatformSidebar.tsx`, `webui/src/pages/BrainPage.tsx`, `webui/src/pages/BrainWorkspacePage.tsx`, `webui/src/panorama/PanoramaCanvas.tsx`, `webui/src/panorama/panorama.css`, `webui/src/panoramaNavigation.ts`, `webui/src/pages/AiEngineeringPage.tsx`, `webui/src/AppShell.sidebar.test.tsx`, `webui/src/pages/BrainPage.test.tsx`, `webui/src/PanoramaRecovery.test.tsx`, `webui/src/panorama/PanoramaView.test.tsx`, `webui/src/panoramaNavigation.test.ts`。

**Interfaces:** 三处入口的 `href` 均为 `platformPath('/ai-notes')`，`target='_blank'`，`rel='noopener noreferrer'`；不触发 `navigate('/ai-notes')`。

- [ ] **Step 1: Write failing tests.** 三处入口断言新页签属性；平台原工作页及草稿在入口激活后保留；带旧全景状态的笔记路由仍独立。
- [ ] **Step 2: Run red.** `cd webui && ./node_modules/.bin/vitest run src/AppShell.sidebar.test.tsx src/pages/BrainPage.test.tsx src/PanoramaRecovery.test.tsx`；预期缺少新页签属性。
- [ ] **Step 3: Implement.** 侧栏笔记项与全景动作使用新页签原生链接，AI 助手首页删除拦截点击并移除已无用途的 `onOpenAiNotes` 回调；全景工作区分组排除笔记。
- [ ] **Step 4: Run green.** 重跑上述测试；预期通过。
- [ ] **Step 5: Commit.** 提交入口及回归测试。

### Task 3: 构建、鉴权回归与发布

**Files:** 前两项代码、`deploy/sdk/overlay_guard.py`（只执行，不修改）。

**Interfaces:** 产物仍是 `orbbec-agent-platform:<SHA>`；线上 `/ai-notes` 与文章深链返回 HTML，匿名文章 API 返回 401。

- [ ] **Step 1: Verify locally.** `cd webui && npm run build && ./node_modules/.bin/vitest run src/auth.test.ts src/router.test.ts src/AppShell.sidebar.test.tsx src/pages/BrainPage.test.tsx src/PanoramaRecovery.test.tsx`；再运行 AI Notes 登录后端用例。
- [ ] **Step 2: Review.** 检查独立框架、链接语义、鉴权边界及 `git diff --check`。
- [ ] **Step 3: Release exact commit.** 从远端主线准确提交构建；`overlay_guard.py` 验证仅 API 镜像和发布标识变化；以现有 Compose 文件顺序只重建 API。
- [ ] **Step 4: Verify production.** 检查健康、笔记页面、匿名 API 401、登录配置、静态资源哈希及其他容器状态；用户在钉钉内验收页面交互。
