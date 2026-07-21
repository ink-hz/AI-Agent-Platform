# Source-Language UI Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace translated Agent identities and marketing-heavy dashboard copy with the approved source-language naming system while preserving Chinese descriptions and all existing runtime and usage behavior.

**Architecture:** Keep `AgentCatalog` as the identity source and update only its display metadata. Add one typed frontend copy catalog so navigation, metrics, states, loading, error, and empty copy share a single reviewed vocabulary; presentation components consume that catalog without changing API types or data flow.

**Tech Stack:** Python 3.11, YAML, pytest, React 19, TypeScript, Vitest, Vite, CSS

## Global Constraints

- Agent names and professional terms remain English; Chinese descriptions and explanatory messages remain Chinese.
- Never append “助手” to an Agent name or translate Prospecting, Inbound, Voice, GTM, Intelligence, or FAE.
- Do not display “AI 团队”, “团队成员”, “今天的 AI 团队”, or “数字员工” in production UI.
- Do not modify `bot_id`, aliases, API fields, statistics, status derivation, polling, or Flywheel access.
- Do not add controls, links, task dispatch, restart actions, or synthetic data.
- Do not modify or restart MetaBot processes; restart only Platform after building.
- Preserve unrelated changes in `backend/app/health/normalizer.py`, `backend/tests/test_health_normalizer.py`, and all existing untracked files.

---

### Task 1: Source-language Agent catalog

**Files:**
- Modify: `backend/tests/test_fleet_catalog.py`
- Modify: `backend/app/fleet/catalog.yaml`

**Interfaces:**
- Consumes: `AgentCatalog.default().profile(bot_id, fallback_name) -> AgentProfile`
- Produces: the exact name, Domain, Glyph, and Chinese description approved in `docs/superpowers/specs/2026-07-21-source-language-ui-copy-design.md`

- [ ] **Step 1: Write the failing catalog identity test**

Replace the loose identity assertions with an exact display map:

```python
EXPECTED_IDENTITIES = {
    "feishu-default": ("Feishu Default", "Feishu", "FS", "承接飞书默认会话与日常协作任务。"),
    "hr-bot": ("HR", "HR", "HR", "处理招聘、人事与员工服务相关工作。"),
    "marketing-prospecting-bot": (
        "Marketing Prospecting", "Marketing", "PRO", "发现、筛选并跟进潜在客户线索。"
    ),
    "marketing-inbound-bot": (
        "Marketing Inbound", "Marketing", "IN", "处理入站线索、内容触达与客户咨询。"
    ),
    "marketing-voice-bot": (
        "Marketing Voice", "Marketing", "VO", "处理语音触达、通话沟通与结果整理。"
    ),
    "fae-bot": ("FAE", "FAE", "FAE", "处理产品咨询、问题诊断与现场应用。"),
    "test-bot": ("Test", "System", "T", "用于接口联调、集成测试与运行验证。"),
    "marketing-gtm-bot": (
        "Marketing GTM", "Marketing", "GTM", "负责市场进入策略、节奏规划与执行协同。"
    ),
    "marketing-intelligence-bot": (
        "Marketing Intelligence", "Marketing", "INT", "收集并整理市场动态与竞争信息。"
    ),
}


def test_catalog_has_approved_identity_for_all_current_bots():
    catalog = AgentCatalog.default()

    actual = {
        bot_id: (
            catalog.profile(bot_id, bot_id).name,
            catalog.profile(bot_id, bot_id).domain,
            catalog.profile(bot_id, bot_id).glyph,
            catalog.profile(bot_id, bot_id).description,
        )
        for bot_id in CURRENT_BOT_IDS
    }

    assert actual == EXPECTED_IDENTITIES
    assert all("助手" not in identity[0] for identity in actual.values())
```

- [ ] **Step 2: Run the catalog test and verify RED**

Run from `backend/`:

```bash
.venv/bin/pytest tests/test_fleet_catalog.py::test_catalog_has_approved_identity_for_all_current_bots -q
```

Expected: FAIL because current names include translated Chinese names and “助手”.

- [ ] **Step 3: Update the YAML display catalog**

Set the nine profiles to the exact values in `EXPECTED_IDENTITIES`. Keep every profile key, `accent`, alias, and unresolved alias unchanged. The first profile must have this shape and the remaining eight follow the same exact map:

```yaml
profiles:
  feishu-default:
    name: Feishu Default
    domain: Feishu
    description: 承接飞书默认会话与日常协作任务。
    glyph: FS
    accent: collaboration
```

- [ ] **Step 4: Run all catalog and service tests**

Run from `backend/`:

```bash
.venv/bin/pytest tests/test_fleet_catalog.py tests/test_fleet_service.py -q
```

Expected: all tests PASS; Bot aliases and real usage aggregation remain unchanged.

- [ ] **Step 5: Commit the catalog change**

```bash
git add backend/app/fleet/catalog.yaml backend/tests/test_fleet_catalog.py
git commit -m "refactor: use source-language Agent names"
```

---

### Task 2: Reviewed dashboard copy system

**Files:**
- Create: `webui/src/copy.ts`
- Create: `webui/src/copy.test.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/FleetAgentCard.tsx`
- Modify: `webui/src/FleetAgentCard.test.tsx`
- Modify: `webui/src/UsageTrend.tsx`
- Modify: `webui/src/UsageTrend.test.tsx`
- Modify: `webui/src/fleet.ts`
- Modify: `webui/src/fleet.test.ts`

**Interfaces:**
- Produces: `UI_COPY`, a readonly object containing reviewed navigation, overview, metrics, insight, card, status, loading, empty, and failure strings
- Consumes: existing `FleetOverview`, `FleetAgent`, formatting functions, polling, and partial-data state without changing them

- [ ] **Step 1: Write the failing copy catalog test**

Create `webui/src/copy.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { UI_COPY } from "./copy";


function allStrings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(allStrings);
  if (value && typeof value === "object") {
    return Object.values(value).flatMap(allStrings);
  }
  return [];
}


describe("reviewed UI copy", () => {
  it("uses the approved source-language product vocabulary", () => {
    expect(UI_COPY.navigation).toEqual(["Overview", "Agents", "Sessions", "Flywheel"]);
    expect(UI_COPY.hero.title).toBe("Agent Overview");
    expect(UI_COPY.summary.metrics).toEqual([
      "Agents", "Online", "Total Conversations", "Last 7 Days",
    ]);
    expect(UI_COPY.agent.fields).toEqual([
      "Total Conversations", "Last 7 Days", "Uptime", "Last Activity", "Recent",
    ]);
  });

  it("contains no rejected translated or marketing-heavy labels", () => {
    const copy = allStrings(UI_COPY).join(" ");
    for (const rejected of ["助手", "AI 团队", "团队成员", "今天的 AI 团队", "数字员工"]) {
      expect(copy).not.toContain(rejected);
    }
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run from `webui/`:

```bash
npm test -- copy.test.ts
```

Expected: FAIL because `copy.ts` does not exist.

- [ ] **Step 3: Create the typed copy catalog**

Create `webui/src/copy.ts` with the complete reviewed vocabulary:

```typescript
export const UI_COPY = {
  navigation: ["Overview", "Agents", "Sessions", "Flywheel"],
  readOnly: "Read-only",
  hero: {
    eyebrow: "AGENT OPERATIONS",
    title: "Agent Overview",
    description: "查看 Agent 运行状态、真实使用量和最近活动。",
    running: (count: number) => `${count} Agents 运行中`,
    attention: (count: number) => `${count} Agents 需要关注`,
    loading: "正在读取 Agent 状态",
  },
  summary: {
    eyebrow: "OPERATIONS",
    title: "Fleet Snapshot",
    metrics: ["Agents", "Online", "Total Conversations", "Last 7 Days"],
    updated: "最后更新",
    agentsHint: "已纳入 Platform",
    activeHint: (count: number) => `${count} Agents 最近有真实活动`,
    totalHint: "数据来自 Flywheel",
  },
  insights: {
    eyebrow: "USAGE",
    trend: "7-Day Trend",
    ranking: "Active Agents",
    rankingHint: "按真实对话排序",
    conversations: (count: string) => `${count} 次对话`,
    emptyTrend: "等待新的真实对话数据",
    emptyRanking: "近 7 天还没有真实对话",
  },
  agent: {
    sectionEyebrow: "CATALOG",
    sectionTitle: "Agents",
    fields: ["Total Conversations", "Last 7 Days", "Uptime", "Last Activity", "Recent"],
    refresh: (count: number) => `${count} Agents · 每 10 秒自动刷新`,
    emptyRecent: "尚无真实对话记录",
  },
  states: {
    active: "Active",
    online: "Online",
    degraded: "Degraded",
    offline: "Offline",
    checking: "Checking",
  },
  failures: {
    platform: "Platform 接口暂不可用，显示最后一次成功数据并继续重试。",
    runtime: "Agent 状态暂不可用，使用数据仍可查看。",
    runtimeStale: "Agent 状态超过 30 秒未更新，显示最后一次成功状态。",
    usage: "Flywheel 暂不可用，显示最后一次成功数据；不使用模拟数据。",
    usageTitle: "对话数据暂不可用",
    usageDescription: "Agent 状态仍在更新，Platform 会继续尝试读取 Flywheel。",
  },
  loading: {
    title: "正在加载 Agent Overview",
    failedTitle: "暂时无法读取 Agent 数据",
    description: "正在汇总 Agent 状态和真实对话数据。",
    retry: "Platform 会继续自动重试。",
  },
  footer: "只读展示，不控制 Agent",
} as const;
```

- [ ] **Step 4: Update component tests before component code**

In `FleetAgentCard.test.tsx`, change the fixture to `name: "HR"`, `domain: "HR"`, `description: "处理招聘、人事与员工服务相关工作。"`, and require the five English field labels plus `Active`.

In `UsageTrend.test.tsx`, require `USAGE` and `7-Day Trend` instead of the translated headings.

In `fleet.test.ts`, require `FLEET_STATE_META.active.label === "Active"` and the remaining four approved English state labels.

Run from `webui/`:

```bash
npm test -- FleetAgentCard.test.tsx UsageTrend.test.tsx fleet.test.ts
```

Expected: FAIL because components still render the old copy.

- [ ] **Step 5: Make all visible components consume `UI_COPY`**

Update `App.tsx`, `FleetAgentCard.tsx`, `UsageTrend.tsx`, and `fleet.ts` so every reviewed visible label comes from `UI_COPY`. Preserve numeric formatting, relative time formatting, failure behavior, polling, sorting, chart rendering, and API requests exactly as they are.

Required visible results include:

```text
Overview / Agents / Sessions / Flywheel
AGENT OPERATIONS / Agent Overview
OPERATIONS / Fleet Snapshot
Agents / Online / Total Conversations / Last 7 Days
USAGE / 7-Day Trend / Active Agents
CATALOG / Agents
Active / Online / Degraded / Offline / Checking
```

The hero description, metric explanations, errors, loading states, empty states, relative time, and footer remain concise Chinese as specified.

- [ ] **Step 6: Run all frontend tests and production build**

Run from `webui/`:

```bash
npm test
npm run build
npm audit --omit=dev
```

Expected: all tests PASS, Vite production build succeeds, and audit reports zero vulnerabilities.

- [ ] **Step 7: Check the production bundle vocabulary**

Run from the repository root:

```bash
rg -n "助手|AI 团队|团队成员|今天的 AI 团队|数字员工" webui/dist/assets/*.js
```

Expected: no matches.

- [ ] **Step 8: Commit the frontend copy change**

```bash
git add webui/src/App.tsx webui/src/FleetAgentCard.tsx webui/src/FleetAgentCard.test.tsx webui/src/UsageTrend.tsx webui/src/UsageTrend.test.tsx webui/src/fleet.ts webui/src/fleet.test.ts webui/src/copy.ts webui/src/copy.test.ts
git commit -m "refactor: standardize Platform UI copy"
```

---

### Task 3: Production deployment and acceptance

**Files:**
- Verify: `webui/dist/`
- Verify: LaunchAgent `com.orbbec.ai-agent-platform`
- Verify: `GET http://127.0.0.1:8000/api/fleet/overview`

**Interfaces:**
- Consumes: built static assets and catalog loaded at Platform startup
- Produces: the reviewed source-language UI at `http://127.0.0.1:8000`

- [ ] **Step 1: Record MetaBot process identity before deployment**

```bash
ps -axo pid,etime,command | rg '/AgentRuntime/metabot/src/index.ts' | rg -v 'rg '
curl -fsS http://127.0.0.1:8000/api/cluster/status
```

Expected: nine MetaBot processes and nine healthy instances.

- [ ] **Step 2: Restart only Platform**

```bash
launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform
```

Expected: Platform restarts; no MetaBot PID changes.

- [ ] **Step 3: Verify live identities and data**

```bash
curl -fsS http://127.0.0.1:8000/api/fleet/overview | jq '{summary, identities: [.agents[] | {id, name, domain, glyph}]}'
```

Expected: nine Agents, healthy real usage sources, and all nine identities exactly match the spec table.

- [ ] **Step 4: Verify live bundle and forbidden vocabulary**

```bash
curl -fsS http://127.0.0.1:8000/ | rg 'assets/index-'
rg -q "Agent Overview" webui/dist/assets/*.js
if rg -q "助手|AI 团队|团队成员|今天的 AI 团队|数字员工" webui/dist/assets/*.js; then exit 1; fi
```

Expected: current hashed assets are served, `Agent Overview` exists, and rejected vocabulary is absent.

- [ ] **Step 5: Run final merged verification**

Run backend tests from `backend/` and frontend checks from `webui/`:

```bash
.venv/bin/pytest -q
npm test
npm run build
npm audit --omit=dev
```

Expected: backend and frontend suites pass, build succeeds, and audit reports zero vulnerabilities.

- [ ] **Step 6: Verify unrelated work remains untouched**

```bash
git status --short
git diff --check
```

Expected: only the user's pre-existing unrelated modified and untracked files remain; no whitespace errors.
