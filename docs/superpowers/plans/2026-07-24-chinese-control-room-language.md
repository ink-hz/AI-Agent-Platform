# Orbbec Agent Platform 中文控制台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 WebUI 从偏海外 SaaS、开源 Admin 模板的英文表达改造成中文优先的 Agent 系统监控大盘，同时完整保留技术名词、Agent 身份和业务原文。

**Architecture:** 后端 API、路由和原始数据保持不变。`copy.ts` 负责静态系统文案和枚举展示映射，现有 `fleet.ts`、`status.ts`、`operations.ts` 继续负责各自领域的中文数字与时间格式化；页面只消费这些统一结果，不自行创造翻译。改造按全局框架、总览、Agent、Session/Trace、运行记录和视觉收口分批完成，每批都有渲染测试与独立提交。

**Tech Stack:** React 19、TypeScript 5.6、Vitest 2.1、jsdom、Vite 7、原生 CSS、Intl `zh-CN`。

## Global Constraints

- 系统导航、页面标题、栏目、状态、操作、加载、空状态、错误和日期表达使用自然中文。
- `Agent`、`Bot`、`Session`、`Trace`、`Skill`、`Backend`、Agent 名称、模型名和业务原文不翻译。
- 后端枚举、API 字段、URL 和 readiness 计算不变；只在前端做显示映射。
- 删除装饰性全大写英文 eyebrow、永久 `Read-only` 标签和“只读展示，不控制 Agent”页脚口号。
- 保留系统监控大盘、白色专业界面、Orbbec 蓝、卡片重量和状态色。
- 日期和相对时间使用 `zh-CN`；生命周期继续依据 `live_since`，当前进程 uptime 继续单独展示。
- 不新增第三方字体、图标库、UI 框架、权限、控制按钮、告警、部署或调度能力。
- Agent/Session/Trace 原始内容必须逐字保留，不得由 UI 文案层改写。
- 保留 Session 返回筛选条件和滚动位置的既有行为。
- 不修改或暂存用户已有的 `backend/app/health/normalizer.py`、`backend/tests/test_health_normalizer.py`、`.claude/`、`registry.local.yaml`、本地 Logo 资产及其他无关 dirty files。

---

### Task 1: 建立中文系统文案和格式化底座

**Files:**
- Modify: `webui/src/copy.ts`
- Modify: `webui/src/copy.test.ts`
- Modify: `webui/src/fleet.ts`
- Modify: `webui/src/fleet.test.ts`
- Modify: `webui/src/operations.ts`
- Modify: `webui/src/operations.test.ts`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`

**Interfaces:**
- Consumes: existing `ReadinessStatus`, `RuntimeFreshness`, `RuntimeChannelStatus`, `FleetState`, `LifecycleBasis` enums without changing their values.
- Produces: centralized `UI_COPY`, `readinessLabel()`, `runtimeFreshnessLabel()`, `channelStatusLabel()`, and Chinese date/duration helpers for all later tasks.

- [ ] **Step 1: Write failing copy and enum-display tests**

Add exact assertions to `copy.test.ts`:

```ts
import {
  UI_COPY,
  channelStatusLabel,
  readinessLabel,
  readinessReasonLabel,
  runtimeFreshnessLabel,
} from "./copy";

it("uses Chinese system copy while preserving agreed technical nouns", () => {
  expect(UI_COPY.navigation).toEqual(["总览", "Agent", "Session", "运行记录"]);
  expect(UI_COPY.hero.title).toBe("Agent 集群总览");
  expect(UI_COPY.agent.sectionTitle).toBe("Agent 运行情况");
  expect(JSON.stringify(UI_COPY)).not.toMatch(/FLEET DIRECTORY|AGENT OPERATIONS|Read-only/);
  expect(JSON.stringify(UI_COPY)).toMatch(/Agent|Session|Trace|Backend/);
});

it("maps runtime enums for display without changing their source values", () => {
  expect(readinessLabel("Ready")).toBe("正常");
  expect(readinessLabel("Busy")).toBe("忙碌");
  expect(readinessLabel("Limited")).toBe("受限");
  expect(readinessLabel("Offline")).toBe("离线");
  expect(readinessLabel("Unknown")).toBe("未知");
  expect(readinessReasonLabel("Ready")).toBe("运行环境和主要 Channel 均正常");
  expect(channelStatusLabel("connected")).toBe("已连接");
  expect(runtimeFreshnessLabel("stale")).toBe("数据已过期");
});
```

- [ ] **Step 2: Write failing Chinese time and title tests**

Update `fleet.test.ts`, `operations.test.ts`, and `documentTitle.test.tsx` with exact expectations:

```ts
expect(formatCount(12345)).toBe("12,345");
expect(formatLifecycleDate("2026-06-17T02:00:00Z")).toBe("2026年6月17日");
expect(formatDaysInProduction("2026-06-17T02:00:00Z", now)).toBe("已运行 34 天");
expect(formatLastUpdated("2026-07-21T23:00:00Z", now)).toBe("3小时前");
expect(formatRuntimeDuration(90_061)).toBe("1天 1小时");
expect(formatLifecycleBasis("earliest_session")).toBe("依据最早采集的 Session");
expect(routeDocumentTitle({ name: "activity" })).toBe("运行记录 · Orbbec Agent Platform");
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test -- src/copy.test.ts src/fleet.test.ts src/operations.test.ts src/documentTitle.test.tsx
```

Expected: FAIL because the centralized runtime label helpers do not exist and lifecycle/title helpers still return English.

- [ ] **Step 4: Implement the centralized language contract**

Replace the current English static copy and add typed display maps in `copy.ts`:

```ts
import type {
  ReadinessStatus,
  RuntimeChannelStatus,
  RuntimeFreshness,
} from "./types";

export const UI_COPY = {
  navigation: ["总览", "Agent", "Session", "运行记录"],
  navigationLabel: "主导航",
  hero: {
    title: "Agent 集群总览",
    description: "查看已接入 Agent 的运行状态、真实使用情况和最近运行记录。",
    running: (count: number) => `${count} 个 Agent 正常运行`,
    attention: (count: number) => `${count} 个 Agent 需要关注`,
    loading: "正在读取 Agent 状态",
  },
  summary: {
    title: "集群概况",
    metrics: ["Agent 数量", "正常运行", "累计对话", "近 7 天对话"],
    updated: "更新时间",
    agentsHint: "已接入 Platform",
    activeHint: (count: number) => `${count} 个 Agent 最近有真实活动`,
    totalHint: "数据来自 Flywheel",
  },
  insights: {
    trend: "近 7 天使用趋势",
    ranking: "活跃 Agent",
    rankingHint: "按真实对话数量排序",
    conversations: (count: string) => `${count} 次对话`,
    emptyTrend: "近 7 天暂无真实对话",
    emptyRanking: "近 7 天暂无活跃 Agent",
  },
  agent: {
    sectionTitle: "Agent 运行情况",
    fields: ["累计对话", "近 7 天", "已上线", "最近更新", "最近工作"],
    refresh: (count: number) => `${count} 个 Agent · 每 10 秒自动刷新`,
    emptyRecent: "暂无真实对话记录",
  },
  states: {
    active: "活跃", online: "在线", degraded: "异常",
    offline: "离线", checking: "检查中", unknown: "未知",
  },
  failures: {
    platform: "Platform 暂时无法获取最新状态，当前显示上一次成功数据并继续重试。",
    usage: "Flywheel 暂时不可用，当前显示上一次成功数据。",
    usageTitle: "对话数据暂不可用",
    usageDescription: "Agent 状态仍在更新，Platform 会继续读取 Flywheel。",
  },
  loading: {
    title: "正在加载 Agent 集群总览",
    failedTitle: "暂时无法读取 Agent 数据",
    description: "正在汇总 Agent 状态和真实对话数据。",
    retry: "Platform 会继续自动重试。",
  },
} as const;

const READINESS_LABELS: Record<ReadinessStatus, string> = {
  Ready: "正常", Busy: "忙碌", Limited: "受限", Offline: "离线", Unknown: "未知",
};
const FRESHNESS_LABELS: Record<RuntimeFreshness, string> = {
  live: "实时", stale: "数据已过期", unavailable: "暂不可用",
};
const CHANNEL_LABELS: Record<RuntimeChannelStatus, string> = {
  connected: "已连接", connecting: "连接中", reconnecting: "正在重连",
  failed: "连接失败", unknown: "未知",
};

export const readinessLabel = (value: ReadinessStatus) => READINESS_LABELS[value];
export const readinessReasonLabel = (value: ReadinessStatus) => ({
  Ready: "运行环境和主要 Channel 均正常",
  Busy: "Agent 正在处理任务",
  Limited: "部分运行能力或主要 Channel 受限",
  Offline: "Agent 当前离线",
  Unknown: "当前观测信息不足",
} satisfies Record<ReadinessStatus, string>)[value];
export const runtimeFreshnessLabel = (value: RuntimeFreshness) => FRESHNESS_LABELS[value];
export const channelStatusLabel = (value: RuntimeChannelStatus) => CHANNEL_LABELS[value];
```

- [ ] **Step 5: Convert domain formatters to `zh-CN`**

In `fleet.ts` and `operations.ts`, retain the existing function signatures and replace English outputs with these rules:

```ts
export function formatCount(value: number | null): string {
  return value === null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
}

export function formatLifecycleDate(value: string | null): string {
  if (value === null) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "long", day: "numeric", timeZone: "Asia/Shanghai",
  }).format(date);
}

export function formatDaysInProduction(value: string | null, now = new Date()): string {
  if (value === null) return "未记录";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "未记录";
  const days = Math.floor(Math.max(0, now.getTime() - timestamp) / 86_400_000);
  return days === 0 ? "今天上线" : `已运行 ${days} 天`;
}

export function formatLastUpdated(value: string | null, now = new Date()): string {
  if (value === null || Number.isNaN(new Date(value).getTime())) return "未记录";
  const seconds = Math.max(0, Math.floor((now.getTime() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "刚刚";
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}分钟前`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3_600)}小时前`;
  if (seconds < 2_592_000) return `${Math.floor(seconds / 86_400)}天前`;
  return formatLifecycleDate(value);
}
```

Use `zh-CN` plus `timeZone: "Asia/Shanghai"` for exact dates in `operations.ts`. Keep API timestamps unchanged.

- [ ] **Step 6: Convert document titles and verify GREEN**

Use the following route titles in `documentTitle.ts`:

```ts
case "agents": return `Agent · ${PLATFORM_TITLE}`;
case "agent": return `Agent 详情 · ${PLATFORM_TITLE}`;
case "agent-runtime": return `运行详情 · ${PLATFORM_TITLE}`;
case "sessions": return `Session · ${PLATFORM_TITLE}`;
case "session": return `Session 回放 · ${PLATFORM_TITLE}`;
case "activity": return `运行记录 · ${PLATFORM_TITLE}`;
```

Run the focused command from Step 3. Expected: PASS.

- [ ] **Step 7: Commit the language foundation**

```bash
git add webui/src/copy.ts webui/src/copy.test.ts webui/src/fleet.ts webui/src/fleet.test.ts webui/src/operations.ts webui/src/operations.test.ts webui/src/documentTitle.ts webui/src/documentTitle.test.tsx
git commit -m "feat: establish Chinese control room language"
```

---

### Task 2: Convert the application shell and Overview

**Files:**
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/pages/OverviewPage.tsx`
- Modify: `webui/src/components/DailyBrief.tsx`
- Modify: `webui/src/UsageTrend.tsx`
- Modify: `webui/src/FleetAgentCard.tsx`
- Modify: `webui/src/dashboard.test.ts`
- Modify: `webui/src/FleetAgentCard.test.tsx`
- Modify: `webui/src/UsageTrend.test.tsx`
- Modify: `webui/src/operationsUi.test.tsx`

**Interfaces:**
- Consumes: `UI_COPY` and Chinese formatters from Task 1; existing fleet and operations API payloads unchanged.
- Produces: Chinese global navigation and Overview while preserving Agent names, descriptions, models, recent summaries, and event titles verbatim.

- [ ] **Step 1: Write failing shell and Overview rendering expectations**

Update the relevant tests to require:

```ts
expect(container.querySelector("nav")?.textContent).toBe("总览AgentSession运行记录");
expect(container.querySelector(".readonly-tag")).toBeNull();
expect(container.querySelector(".site-foot")?.textContent).toBe("Orbbec Agent Platform");
expect(container.querySelector("h1")?.textContent).toBe("Agent 集群总览");
expect(container.textContent).toContain("集群概况");
expect(container.textContent).toContain("近 7 天使用趋势");
expect(container.textContent).toContain("Agent 运行情况");
expect(container.textContent).not.toMatch(/Fleet Snapshot|Active Agents|Last 24 Hours|Read-only/);
```

In `FleetAgentCard.test.tsx`, keep the English fixture description and name, then assert both remain unchanged while labels become Chinese:

```ts
expect(html).toContain("Marketing Prospecting");
expect(html).toContain("Finds high-fit target accounts");
expect(html).toContain("累计对话");
expect(html).toContain("近 7 天");
expect(html).toContain("已运行 33 天");
expect(html).toContain("上线于 2026年6月17日");
expect(html).not.toContain("Total Conversations");
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test -- src/dashboard.test.ts src/FleetAgentCard.test.tsx src/UsageTrend.test.tsx src/operationsUi.test.tsx
```

Expected: FAIL on current English navigation, headings, cards, Daily Brief, and permanent read-only label.

- [ ] **Step 3: Implement the shell and Overview copy**

In `AppShell.tsx`, define navigation exactly as:

```ts
const NAVIGATION = [
  { label: "总览", path: "/", section: "overview" },
  { label: "Agent", path: "/agents", section: "agents" },
  { label: "Session", path: "/sessions", section: "sessions" },
  { label: "运行记录", path: "/activity", section: "activity" },
] as const;
```

Extend `routeSection()` and its return type in `router.ts` so the existing `/activity` route returns `"activity"`; update `router.test.ts` to expect Activity to be a primary navigation section. Do not change route paths or parsing.

Remove `.readonly-tag` entirely and render the footer as only:

```tsx
<footer className="site-foot"><span>Orbbec Agent Platform</span></footer>
```

In `App.tsx`, replace pending and redirect copy with “页面不存在 / 返回 Agent 集群总览” and “正在打开 Session”.

In `OverviewPage.tsx`, remove eyebrow `<p>` nodes and use `UI_COPY` titles directly. Keep `agent.name`, `agent.description`, `agent.recent_summary`, `event.title`, and `event.summary` untouched.

- [ ] **Step 4: Convert Daily Brief, trend, ranking, and Agent-card labels**

Use these exact system labels:

```tsx
// DailyBrief headings
<h2>今日运行摘要</h2>
<h3>需要关注</h3>
<h3>近 24 小时</h3>
<PlatformLink href="/activity">查看全部运行记录 →</PlatformLink>

// Healthy/empty text
<strong>暂无严重问题</strong>
<p>当前关键运行检查均正常。</p>
<p>近 24 小时暂无重要变化。</p>
```

Keep event titles and Agent names from the API in their original language. Change `FleetAgentCard` field labels through `UI_COPY.agent.fields`, display `上线于 ${formatLifecycleDate(...)}`, and do not translate model/backend values.

- [ ] **Step 5: Verify Overview GREEN and commit**

Run the command from Step 2. Expected: PASS.

```bash
git add webui/src/AppShell.tsx webui/src/App.tsx webui/src/router.ts webui/src/router.test.ts webui/src/pages/OverviewPage.tsx webui/src/components/DailyBrief.tsx webui/src/UsageTrend.tsx webui/src/FleetAgentCard.tsx webui/src/dashboard.test.ts webui/src/FleetAgentCard.test.tsx webui/src/UsageTrend.test.tsx webui/src/operationsUi.test.tsx
git commit -m "feat: localize Agent cluster overview"
```

---

### Task 3: Convert Agent directory, profile, and Runtime detail

**Files:**
- Modify: `webui/src/components/AgentDirectoryCard.tsx`
- Modify: `webui/src/components/AgentDirectorySections.tsx`
- Modify: `webui/src/pages/AgentsPage.tsx`
- Modify: `webui/src/pages/AgentDetailPage.tsx`
- Modify: `webui/src/pages/AgentRuntimePage.tsx`
- Modify: `webui/src/observability.test.tsx`
- Modify: `webui/src/pages/AgentDetailPage.test.tsx`
- Modify: `webui/src/pages/AgentRuntimePage.test.tsx`

**Interfaces:**
- Consumes: `readinessLabel()`, `readinessReasonLabel()`, `channelStatusLabel()`, `runtimeFreshnessLabel()` and Chinese lifecycle formatters from Task 1.
- Produces: localized Agent monitoring views; readiness CSS class names and backend enum values remain source-valued.

- [ ] **Step 1: Write failing Agent-page tests**

Require these visible labels while preserving fixture business content:

```ts
expect(html).toContain("Agent 列表");
expect(html).toContain("业务 Agent");
expect(html).toContain("系统 Agent");
expect(html).toContain("累计 Session");
expect(html).toContain("累计对话");
expect(html).toContain("Production engineering Agent");
expect(html).not.toContain("FLEET DIRECTORY");

expect(detail.textContent).toContain("返回 Agent 列表");
expect(detail.textContent).toContain("运行状态");
expect(detail.textContent).toContain("正常");
expect(detail.textContent).toContain("查看运行详情");
expect(detail.textContent).toContain("最近运行记录");
expect(detail.textContent).toContain("最近 Session");
expect(detail.textContent).toContain("claude-opus-4-8 · PTY");
```

- [ ] **Step 2: Write failing Runtime-detail tests**

```ts
expect(runtime.textContent).toContain("运行详情");
expect(runtime.textContent).toContain("当前运行状态");
expect(runtime.textContent).toContain("运行环境");
expect(runtime.textContent).toContain("当前进程");
expect(runtime.textContent).toContain("进程重启后重新计时");
expect(runtime.textContent).toContain("运行周期");
expect(runtime.textContent).toContain("观测依据");
expect(runtime.textContent).toContain("Claude Opus 4.8");
expect(runtime.textContent).toContain("Backend");
expect(runtime.textContent).not.toMatch(/RUNTIME DETAIL|Observed sources|Production timeline/);
```

- [ ] **Step 3: Run focused tests and verify RED**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test -- src/observability.test.tsx src/pages/AgentDetailPage.test.tsx src/pages/AgentRuntimePage.test.tsx
```

Expected: FAIL on English directory, Runtime, lifecycle, empty-state, and link copy.

- [ ] **Step 4: Implement Agent directory and profile copy**

Use these page structures:

```tsx
// AgentsPage intro
<section className="page-intro">
  <div><h1>Agent 列表</h1><p>查看所有已接入 Agent 的职责、数据来源和真实运行记录。</p></div>
  {agents && <strong>{business.length}<span> 个业务 Agent</span></strong>}
</section>

// Agent Detail section headings
<h2>运行状态</h2>
<PlatformLink href={`/agents/${encodeURIComponent(agent.id)}/runtime`}>查看运行详情 →</PlatformLink>
<h2>最近运行记录</h2>
<h2>最近 Session</h2>
```

Map readiness text through `readinessLabel(runtime.readiness.status)` while retaining the original value in the CSS class. Replace system-generated runtime fallbacks with “运行信息暂不可用”“正在加载运行状态”“尚未观测到 Channel”“未记录运行时间”. Do not alter `agent.name`, `agent.domain`, or `agent.description`.

Render the reason through `readinessReasonLabel(runtime.readiness.status)` instead of showing the backend's English `runtime.readiness.reason`. Map freshness through `runtimeFreshnessLabel()`. Keep `source_kind` and Backend/model/channel values as technical source values; map deployment/freshness badges only when they are Platform-owned states.

- [ ] **Step 5: Implement Runtime-detail copy and evidence presentation**

Use:

```tsx
<h1>{agent.name} 运行详情</h1>
<h2>当前运行状态</h2>
<span>Model</span>
<span>Engine · Backend</span>
<span>Channel</span>
<span>当前进程</span>
<small>进程重启后重新计时</small>
<h2>运行周期</h2>
<dt>生产运行时间</dt>
<dt>上线时间</dt>
<dt>最近更新</dt>
<h2>观测依据</h2>
```

Map model-source explanations to “运行时观测”“最近完成的 Trace”“配置模型 · 尚未在运行中观测”“暂无模型观测”. Map `item.kind` only as a UI category (`process` → “进程”, `runtime` → “运行时”, `trace` → `Trace`) and preserve `item.source` as a secondary technical value. Replace the backend's generic English evidence summaries with source-aware Chinese chrome: `health_probe` → “进程健康检查”, `runtime_observation` → “实时运行观测”, `latest_completed_trace` → “最近完成的 Trace”; unknown sources display “运行观测记录” plus their unmodified source value. Do not show the raw English `item.summary`.

- [ ] **Step 6: Verify Agent views GREEN and commit**

Run the command from Step 3. Expected: PASS.

```bash
git add webui/src/components/AgentDirectoryCard.tsx webui/src/components/AgentDirectorySections.tsx webui/src/pages/AgentsPage.tsx webui/src/pages/AgentDetailPage.tsx webui/src/pages/AgentRuntimePage.tsx webui/src/observability.test.tsx webui/src/pages/AgentDetailPage.test.tsx webui/src/pages/AgentRuntimePage.test.tsx
git commit -m "feat: localize Agent runtime views"
```

---

### Task 4: Convert Session replay and Trace controls

**Files:**
- Modify: `webui/src/pages/SessionsPage.tsx`
- Modify: `webui/src/pages/SessionDetailPage.tsx`
- Modify: `webui/src/components/SessionListItem.tsx`
- Modify: `webui/src/components/TurnCard.tsx`
- Modify: `webui/src/components/TraceTimeline.tsx`
- Modify: `webui/src/senderIdentity.ts`
- Modify: `webui/src/pages/SessionsPage.test.tsx`
- Modify: `webui/src/pages/SessionDetailPage.test.tsx`
- Modify: `webui/src/observability.test.tsx`
- Modify: `webui/src/senderIdentity.test.tsx`
- Modify: `webui/src/trace.test.tsx`

**Interfaces:**
- Consumes: existing Session queries, navigation context, sender identity formatting, and Trace API without changes.
- Produces: Chinese Session UI while preserving query/answer/title/identity/Trace content and exact return-state behavior.

- [ ] **Step 1: Write failing Session-list and navigation tests**

Require:

```ts
expect(page.textContent).toContain("Session");
expect(page.textContent).toContain("查看各 Agent 的真实 Session 和对话记录");
expect(page.textContent).toContain("全部业务 Agent");
expect(page.textContent).toContain("共 168 个 Session");
expect(page.textContent).not.toMatch(/SESSION DIRECTORY|All Business Agents/);
```

Keep the existing test that navigates into a Session and restores the exact query string and scroll position when returning.

- [ ] **Step 2: Write failing replay, Turn, and Trace tests**

```ts
expect(replay.textContent).toContain("Session 回放");
expect(replay.textContent).toContain("用户提问");
expect(replay.textContent).toContain("Agent 回答");
expect(replay.textContent).toContain("查看 Trace");
expect(replay.textContent).toContain(originalQuestion);
expect(replay.textContent).toContain(originalAnswer);
expect(replay.textContent).not.toMatch(/Question|Answer|View Trace|Evidence detail/);
```

Trace tests must preserve stage names, tool names, durations, and error bodies from the API verbatim while translating only chrome such as “执行链路”“收起 Trace”“正在加载 Trace”“该轮暂无 Trace 详情”.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test -- src/pages/SessionsPage.test.tsx src/pages/SessionDetailPage.test.tsx src/observability.test.tsx src/senderIdentity.test.tsx src/trace.test.tsx src/navigationContext.test.tsx src/sessionNavigation.test.ts
```

Expected: FAIL only on visible English system copy; navigation-context assertions must remain green.

- [ ] **Step 4: Implement Session list and replay copy**

Use “Session”“全部业务 Agent”“共 N 个 Session”“上一页”“下一页”“返回 Session 列表”“Session 回放”“用户提问”“Agent 回答”. In `SessionListItem`, use `Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" })`; replace only empty UI fallbacks with “未命名 Session”.

Do not transform these values:

```tsx
{session.title}
{session.agent_name}
{turn.question}
{turn.answer}
{turn.sender_name}
{turn.sender_department}
{turn.trace_key}
```

- [ ] **Step 5: Implement Turn and Trace chrome copy**

In `TurnCard.tsx`, use:

```tsx
<span>用户提问</span>
<p>{turn.question || "未记录用户提问"}</p>
<span>Agent 回答</span>
<p>{turn.answer || "未记录 Agent 回答"}</p>
<h3>证据</h3>
<p className="availability-note">证据详情：{availabilityLabel(turn.evidence_availability)}</p>
<button>{open ? "收起 Trace" : "查看 Trace"}</button>
```

In `TraceTimeline.tsx`, translate only fixed labels and retain all API-provided step names, summaries, tool inputs, outputs, errors, identifiers, and timestamps unchanged.

In `senderIdentity.ts`, use `Feishu 用户` when no name is captured, `${name} · 部门未记录` when the department is missing, and `另有 ${additional} 人` for additional participants. Preserve captured names and departments exactly.

- [ ] **Step 6: Verify Session/Trace GREEN and commit**

Run the command from Step 3. Expected: PASS, including filter and scroll restoration tests.

```bash
git add webui/src/pages/SessionsPage.tsx webui/src/pages/SessionDetailPage.tsx webui/src/components/SessionListItem.tsx webui/src/components/TurnCard.tsx webui/src/components/TraceTimeline.tsx webui/src/senderIdentity.ts webui/src/pages/SessionsPage.test.tsx webui/src/pages/SessionDetailPage.test.tsx webui/src/observability.test.tsx webui/src/senderIdentity.test.tsx webui/src/trace.test.tsx
git commit -m "feat: localize Session and Trace views"
```

---

### Task 5: Convert Activity, shared states, filters, and paging

**Files:**
- Modify: `webui/src/pages/ActivityPage.tsx`
- Modify: `webui/src/components/OperationalEventItem.tsx`
- Modify: `webui/src/components/DataState.tsx`
- Modify: `webui/src/operations.ts`
- Modify: `webui/src/operationsUi.test.tsx`
- Modify: `webui/src/status.test.ts`

**Interfaces:**
- Consumes: operational event objects and filter query parameters unchanged.
- Produces: localized Activity chrome; API-provided Agent names, event titles, summaries, actor names, and correlation identifiers remain original.

- [ ] **Step 1: Write failing Activity and shared-state tests**

Replace current English expectations with:

```ts
expect(labels).toEqual(["Agent", "事件类型", "级别", "开始时间", "结束时间"]);
expect(options[0]).toBe("全部业务 Agent");
expect(page.querySelector("h1")?.textContent).toBe("运行记录");
expect(page.textContent).toContain("筛选 Agent 的部署、配置、运行状态和数据同步记录");
expect(page.textContent).toContain("加载更多");
expect(page.textContent).not.toMatch(/Activity History|Event type|Severity|From|To/);

expect(error.textContent).toContain("数据暂不可用");
expect(error.textContent).toContain("Platform 暂时无法读取当前页面，Agent 服务不受影响。");
expect(error.textContent).toContain("重试");
```

Keep API fixture event titles such as `AI FAE Agent recovered` unchanged and assert that exact string remains visible.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test -- src/operationsUi.test.tsx src/status.test.ts
```

Expected: FAIL on Activity headings, controls, paging, shared errors, and stale-state copy.

- [ ] **Step 3: Implement Activity and DataState copy**

Use these exact shared states:

```tsx
export function LoadingState({ label = "正在加载数据" }: { label?: string }) {
  return <section className="data-state" aria-live="polite"><span className="empty-pulse" /><h2>{label}</h2></section>;
}

export function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return <section className="data-state data-error" role="alert">
    <strong>数据暂不可用</strong>
    <p>Platform 暂时无法读取当前页面，Agent 服务不受影响。</p>
    {onRetry && <button onClick={onRetry}>重试</button>}
  </section>;
}
```

In `ActivityPage.tsx`, use “运行记录”“事件类型”“级别”“开始时间”“结束时间”“应用筛选”“清除筛选”“加载更多”“正在加载”. Map severity and event-type enums to Chinese display labels, but keep event titles and summaries untouched. Convert group headings to “今天”“昨天” and Chinese calendar dates.

In `OperationalEventItem.tsx`, map severity chrome to “严重”“需关注”“信息”“已恢复”, change `Source` to “来源”, and show `event.source_kind` as its original technical value instead of title-casing it. Preserve `event.title` and `event.summary` exactly.

- [ ] **Step 4: Localize operational freshness text without changing timestamps**

In `operations.ts`, return:

```ts
return time ? `运行摘要数据已过期 · 最近计算于 ${time}` : "运行摘要数据已过期";
```

Use `zh-CN`/`Asia/Shanghai` for event and filter dates. Keep ISO values in URL query parameters.

- [ ] **Step 5: Verify Activity GREEN and commit**

Run the command from Step 2. Expected: PASS.

```bash
git add webui/src/pages/ActivityPage.tsx webui/src/components/OperationalEventItem.tsx webui/src/components/DataState.tsx webui/src/operations.ts webui/src/operationsUi.test.tsx webui/src/status.test.ts
git commit -m "feat: localize Agent activity views"
```

---

### Task 6: Remove open-source template cues and add regression guards

**Files:**
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`
- Create: `webui/src/languagePolicy.test.ts`

**Interfaces:**
- Consumes: localized DOM structure from Tasks 2–5.
- Produces: Chinese-first typography and a source-level regression guard against reintroducing banned template copy.

- [ ] **Step 1: Write failing visual-contract tests**

Add to `styles.test.ts`:

```ts
expect(rule(":root")).toContain('font-family: "PingFang SC", "Microsoft YaHei"');
expect(styles).not.toContain(".readonly-tag");
expect(rule(".page-intro h1")).not.toContain("text-transform: uppercase");
expect(rule(".runtime-detail-grid")).toContain("background: var(--surface)");
expect(rule(".runtime-fact")).toContain("box-shadow: none");
```

Create `languagePolicy.test.ts` with an explicit production-file list and banned chrome phrases:

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const files = [
  "./AppShell.tsx", "./App.tsx", "./pages/OverviewPage.tsx",
  "./pages/AgentsPage.tsx", "./pages/AgentDetailPage.tsx",
  "./pages/AgentRuntimePage.tsx", "./pages/SessionsPage.tsx",
  "./pages/SessionDetailPage.tsx", "./pages/ActivityPage.tsx",
  "./components/DataState.tsx", "./components/DailyBrief.tsx",
  "./components/TurnCard.tsx", "./components/TraceTimeline.tsx",
];

describe("Chinese control room language policy", () => {
  it("does not reintroduce generic English admin chrome", () => {
    const source = files.map((file) => readFileSync(new URL(file, import.meta.url), "utf8")).join("\n");
    for (const phrase of [
      "Read-only", "FLEET DIRECTORY", "AGENT OPERATIONS", "Fleet Snapshot",
      "Runtime Detail", "Activity History", "Session Replay", "Data unavailable",
    ]) expect(source).not.toContain(phrase);
  });
});
```

- [ ] **Step 2: Run visual/language tests and verify RED**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test -- src/styles.test.ts src/languagePolicy.test.ts
```

Expected: FAIL because the current font stack, read-only CSS, isolated Runtime cards, and English chrome remain.

- [ ] **Step 3: Implement Chinese-first typography and remove dead template styles**

Set:

```css
:root {
  font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
}

.brand-name,
.runtime-fact strong,
.trace-panel code {
  font-family: Inter, "Segoe UI", "PingFang SC", sans-serif;
}
```

Delete `.readonly-tag` and decorative `.eyebrow` rules that no longer have markup. Remove uppercase transforms and wide letter spacing from normal section labels. Keep technical IDs in the existing monospace stack.

- [ ] **Step 4: Consolidate Runtime fact cards without changing information**

Use one shared panel with dividers:

```css
.runtime-detail-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--surface);
  box-shadow: 0 12px 30px rgba(20, 51, 89, .08);
}

.runtime-fact {
  min-width: 0;
  padding: 21px;
  border: 0;
  border-right: 1px solid var(--line-soft);
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.runtime-fact:last-child { border-right: 0; }
```

At the existing narrow breakpoint, stack to one column and change the divider to `border-bottom`; retain the current minimum font sizes and card contrast.

- [ ] **Step 5: Verify visual/language GREEN and commit**

Run the command from Step 2. Expected: PASS.

```bash
git add webui/src/styles.css webui/src/styles.test.ts webui/src/languagePolicy.test.ts
git commit -m "style: give control room a Chinese product voice"
```

---

### Task 7: Full verification, production deployment, and GitHub push

**Files:**
- Verify only: `webui/src/**`
- Build output: `webui/dist/**` (ignored deployment artifact)
- Service: `com.orbbec.ai-agent-platform`

**Interfaces:**
- Consumes: all commits from Tasks 1–6.
- Produces: verified production WebUI served by the existing Platform LaunchAgent; backend and MetaBot services remain untouched.

- [ ] **Step 1: Review scope and preserve unrelated work**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git status --short
git diff --check
git diff --name-only HEAD~6..HEAD
```

Expected: only the plan and intended `webui/src` files belong to this change. Existing health normalizer, local registry, `.claude/`, local Logo files, and unrelated docs remain unstaged.

- [ ] **Step 2: Run the complete frontend suite and production build**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test -- --run
npm run build
```

Expected: all tests PASS and Vite produces `dist/index.html`, CSS, and JS assets.

- [ ] **Step 3: Run backend isolation verification**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/pytest -q
```

Expected: all backend tests PASS despite no backend source changes.

- [ ] **Step 4: Restart only the Platform service**

```bash
launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform
```

Wait until health returns 200; do not restart MetaBot, PostgreSQL, FAE, ADMIN, Nginx, or any other service.

- [ ] **Step 5: Verify deployed routes and Chinese production assets**

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/health
for path in / /agents /agents/marketing-inbound-bot /agents/marketing-inbound-bot/runtime /sessions /activity; do
  curl --noproxy '*' -fsS -o /dev/null -w "$path %{http_code}\n" "http://127.0.0.1:8000$path"
done
rg -n 'Agent 集群总览|运行详情|Session 回放|运行记录' webui/dist/assets/*.js
if rg -n 'Read-only|FLEET DIRECTORY|AGENT OPERATIONS|Fleet Snapshot' webui/dist/assets/*.js; then exit 1; fi
launchctl print gui/$(id -u)/com.orbbec.ai-agent-platform | rg 'state =|pid =|last exit code'
```

Expected: health and every SPA route return 200, Chinese copy exists in the deployed JS, banned template phrases are absent, and the LaunchAgent is running.

- [ ] **Step 6: Confirm monitoring data and navigation behavior remain intact**

```bash
for agent_id in marketing-inbound-bot codex-assistant ai-fae-agent ai-admin-agent; do
  curl --noproxy '*' -fsS "http://127.0.0.1:8000/api/agents/${agent_id}/runtime" |
    jq -e --arg id "$agent_id" '.agent_id == $id and (.readiness.status | type == "string")'
done
```

Expected: all four canonical Runtime responses remain valid. Re-run `navigationContext.test.tsx` and `sessionNavigation.test.ts` to prove Session return filters and scroll state are still preserved.

- [ ] **Step 7: Push the verified master branch**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git fetch origin master
git rev-list --left-right --count origin/master...HEAD
git push origin master
```

Expected: remote is not ahead, push succeeds without force, and user-owned uncommitted files remain local and unstaged.

## Plan Self-Review Checklist

- [x] Every spec section is covered by Tasks 1–7.
- [x] Static UI copy and enum labels have one named owner (`copy.ts`).
- [x] Domain time formatters retain their existing signatures and move to `zh-CN`.
- [x] Agent, Session, Trace, model, Backend, and business payloads have explicit preservation tests.
- [x] Session navigation context and scroll restoration are included in regression verification.
- [x] No backend or runtime contract change is planned.
- [x] Visual work is limited to confirmed font, eyebrow, read-only, spacing, and Runtime-card consolidation boundaries.
- [x] Deployment restarts only `com.orbbec.ai-agent-platform`.
- [x] Existing unrelated dirty files are excluded from every commit.
