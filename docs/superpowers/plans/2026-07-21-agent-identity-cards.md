# Agent Identity Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the previous “one card, one Agent” visual identity while preserving a read-only MetaBot cluster monitor.

**Architecture:** Add a pure presentation mapping from runtime instance IDs to friendly Agent identity metadata, then consume it from the existing React dashboard. Keep polling and backend contracts untouched; use CSS to rebuild the card hierarchy and add a static explanatory panel.

**Tech Stack:** React 19, TypeScript, Vitest, Vite, CSS

## Global Constraints

- Do not add Agent entry links, controls, task dispatch, or restart actions.
- Preserve the existing 10-second polling and stale-data behavior.
- Unknown contract instances must render with a generic identity.
- Do not modify backend files or unrelated pre-existing working-tree changes.

---

### Task 1: Agent identity presentation model

**Files:**
- Create: `webui/src/identity.ts`
- Create: `webui/src/identity.test.ts`

**Interfaces:**
- Consumes: runtime `id` and fallback `name` strings from `InstanceStatus`.
- Produces: `agentIdentity(id: string, fallbackName: string): AgentIdentity` with `name`, `domain`, `description`, `glyph`, and `accent`.

- [ ] **Step 1: Write the failing identity tests**

```ts
expect(agentIdentity("hr-bot", "hr-bot")).toEqual({
  name: "HR 助手",
  domain: "人力资源",
  description: "支持招聘、人事与员工服务流程。",
  glyph: "HR",
  accent: "people",
});
expect(agentIdentity("new-bot", "new-bot")).toMatchObject({
  name: "new-bot",
  domain: "MetaBot 实例",
  glyph: "AI",
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npm test -- identity.test.ts`

Expected: FAIL because `./identity` does not exist.

- [ ] **Step 3: Implement the identity map and fallback**

Create a typed `Record<string, AgentIdentity>` for the nine known IDs. Return the matching value or a generic identity using `fallbackName`.

- [ ] **Step 4: Run the test and verify GREEN**

Run: `npm test -- identity.test.ts`

Expected: all identity tests PASS.

### Task 2: Identity-first dashboard cards

**Files:**
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `agentIdentity(instance.id, instance.name)` from Task 1 and existing status formatting helpers.
- Produces: static dashboard introduction and non-interactive identity-first Agent cards.

- [ ] **Step 1: Import and apply identity metadata**

In each instance render, resolve `const identity = agentIdentity(instance.id, instance.name)` and show the glyph, friendly name, domain, and description before operational metrics.

- [ ] **Step 2: Add the dashboard introduction**

Add a semantic section that states the board watches the local runtime-contract fleet, refreshes every 10 seconds through `/api/health`, and is read-only without Agent control.

- [ ] **Step 3: Rebuild card styles**

Use a 44px identity glyph, stronger title hierarchy, an airy description region, three metric chips, and a separated metadata footer. Keep explicit status text and responsive one-column behavior.

- [ ] **Step 4: Verify no interactive entry exists**

Run: `rg -n '<a|<button|href=|onClick=' webui/src/App.tsx`

Expected: no matches.

### Task 3: Verification and deployment

**Files:**
- Generated: `webui/dist/`

**Interfaces:**
- Consumes: completed web UI.
- Produces: tested production assets served by the existing local Platform service.

- [ ] **Step 1: Run the complete frontend suite**

Run: `npm test`

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Build production assets**

Run: `npm run build`

Expected: TypeScript and Vite complete successfully.

- [ ] **Step 3: Verify the deployed page and cluster API**

Run: `curl --noproxy '*' -fsS http://127.0.0.1:8000/` and `curl --noproxy '*' -fsS http://127.0.0.1:8000/api/cluster/status`.

Expected: the page contains `看板说明` and the API reports 9 total, 9 healthy instances.

- [ ] **Step 4: Confirm the Platform service and MetaBots remain healthy**

Run the existing LaunchAgent status check and compare the 9 current health results after deployment.

Expected: Platform responds on port 8000 and all 9 read-only probes remain healthy.
