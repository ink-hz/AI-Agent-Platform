# Agent Data Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic Flywheel metrics dashboard with an Agent-switchable browser for real Sessions and their existing Question, Answer, Evidence, and Trace details.

**Architecture:** Keep `/flywheel` and reuse the canonical `/api/agents` and `/api/sessions` endpoints. Add one focused Agent switcher component and compose it with existing Agent types, Session rows, and data states; do not change backend schemas or introduce universal Feedback concepts.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, existing FastAPI read APIs.

## Global Constraints

- The page is read-only.
- Do not render generic KPI cards, Feedback, Review, Eval, Knowledge Task, or QA modules.
- Do not translate source content.
- Do not add backend endpoints or database changes.
- Preserve existing Agent services and unrelated dirty files.

---

### Task 1: Add the Agent data switcher

**Files:**
- Create: `webui/src/components/AgentDataSwitcher.tsx`
- Create: `webui/src/agentDataBrowser.test.tsx`

**Interfaces:**
- Consumes: `AgentSummary[]`, `selectedId: string`, and `onSelect(id: string): void`.
- Produces: `AgentDataSwitcher`, a keyboard-accessible button list with a visible selected state.

- [ ] **Step 1: Write the failing component test**

Render two Agents and assert that both names and source labels appear, the selected button has `aria-pressed="true"`, and invoking the other button calls `onSelect` with its Agent ID.

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `cd webui && npm test -- --run src/agentDataBrowser.test.tsx`

Expected: FAIL because `AgentDataSwitcher` does not exist.

- [ ] **Step 3: Implement the focused switcher**

Create a horizontal button list using each Agent's glyph, name, source, and accent class. Do not fetch data inside the component.

- [ ] **Step 4: Run the focused test**

Run: `cd webui && npm test -- --run src/agentDataBrowser.test.tsx`

Expected: PASS.

### Task 2: Replace generic Flywheel metrics with selected Agent data

**Files:**
- Modify: `webui/src/pages/FlywheelPage.tsx`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/agentDataBrowser.test.tsx`

**Interfaces:**
- Consumes: `fetchAgents()`, `fetchSessions({agent_id, limit: 50})`, `AgentDataSwitcher`, `SessionListItem`, and existing data-state components.
- Produces: `/flywheel` as an Agent-specific Session browser.

- [ ] **Step 1: Add failing page source-contract tests**

Assert that `FlywheelPage.tsx` uses `AgentDataSwitcher` and `fetchSessions`, and does not reference `fetchFlywheelOverview`, `fetchFlywheelItems`, `Feedback`, `Pending Review`, `Eval Candidates`, or `Daily sync`.

- [ ] **Step 2: Run the test and confirm failure**

Run: `cd webui && npm test -- --run src/agentDataBrowser.test.tsx`

Expected: FAIL because the page still renders global metrics and improvement items.

- [ ] **Step 3: Implement selected-Agent data loading**

Load Agents once, select the first Agent by default, cancel the previous Session request when selection changes, and render the selected Agent context plus its Session list. Preserve explicit loading, failed, and empty states.

- [ ] **Step 4: Add strong responsive styles**

Add switcher, selected state, Agent context, and narrow-screen rules. Remove now-unused Flywheel metric, sync strip, and improvement-card styles.

- [ ] **Step 5: Verify focused behavior and production build**

Run: `cd webui && npm test -- --run src/agentDataBrowser.test.tsx src/observability.test.tsx src/styles.test.ts && npm run build`

Expected: all tests pass and the Vite build succeeds.

### Task 3: Deploy and verify

**Files:**
- No new files.

**Interfaces:**
- Consumes: the built WebUI served by the existing Platform LaunchAgent.
- Produces: a live `/flywheel` Agent data browser without restarting any Agent service.

- [ ] **Step 1: Run the complete frontend suite**

Run: `cd webui && npm test -- --run && npm run build`

Expected: all Vitest files pass and production build succeeds.

- [ ] **Step 2: Commit the implementation**

Commit only the spec, plan, switcher, Flywheel page, tests, API cleanup, and styles. Preserve unrelated dirty files.

- [ ] **Step 3: Restart only Platform and smoke-test**

Restart `com.orbbec.ai-agent-platform`, then verify `/api/health`, `/flywheel`, `/api/agents`, and one Agent-filtered `/api/sessions` request return HTTP 200. Confirm all nine local Agent health endpoints remain HTTP 200.
