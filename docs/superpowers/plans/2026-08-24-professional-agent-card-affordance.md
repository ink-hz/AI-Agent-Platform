# Professional Agent Card Affordance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Professional Agent card obviously clickable, fix the product order to FAE → HR → Marketing → Administration, and verify the intended Agent access grants.

**Architecture:** Keep the existing authorized Catalog API and whole-card links. Change only the directory projection and its CSS: render one continuous, canonically ordered two-column grid, derive an accent from card metadata, render explicit action affordances, and preserve URL allowlisting for external workspaces. Authorization remains server-side and external workspaces remain non-dispatchable.

**Tech Stack:** React 19, TypeScript, CSS, Vitest/jsdom, PostgreSQL authorization functions.

## Global Constraints

- FAE is first, HR second, Marketing third, and Administration last.
- All card surfaces are the click target when enabled.
- External workspace URLs remain restricted to the existing allowlist.
- FAE and Administration remain external-only and never become Brain-dispatchable.
- Mobile remains single-column and keyboard focus must be visible.

---

### Task 1: Directory ordering and card semantics

**Files:**
- Modify: `webui/src/pages/AgentUseDirectoryPage.tsx`
- Test: `webui/src/pages/AgentUsePage.test.tsx`

**Interfaces:**
- Consumes: `AgentCapabilityCard` from the authorized Catalog API.
- Produces: ordered groups and enabled links with an explicit action label and decorative arrow.

- [ ] **Step 1: Write a failing test**

Require a continuous grid without forced group headings, assert FAE is the first card and Administration is the final card, and assert enabled cards expose an action element plus `data-agent-kind` styling hook.

- [ ] **Step 2: Run the focused Vitest test and verify RED**

Run: `npm test -- --run src/pages/AgentUsePage.test.tsx`

Expected: FAIL because the current page combines both external cards under `专业工作区` and has no explicit card affordance structure.

- [ ] **Step 3: Implement the minimal directory projection**

Replace the grouped projection with canonical card ordering and render a `header`, content body, and action footer inside the existing whole-card link. Keep `safeWorkspaceUrl()` unchanged.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `npm test -- --run src/pages/AgentUsePage.test.tsx`

Expected: all tests pass.

### Task 2: Visual click affordance

**Files:**
- Modify: `webui/src/styles.css`
- Test: `webui/src/pages/AgentUsePage.test.tsx`

**Interfaces:**
- Consumes: the semantic classes and `data-agent-kind` attributes from Task 1.
- Produces: visible border/accent, elevation, hover/focus movement, button-like footer, and responsive layout.

- [ ] **Step 1: Extend the failing test**

Assert that every enabled card has `.agent-use-card-action`, `.agent-use-card-arrow`, and a meaningful `aria-label` on its link.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm test -- --run src/pages/AgentUsePage.test.tsx`

Expected: FAIL before the semantic hooks exist.

- [ ] **Step 3: Implement the card CSS**

Use a 3px accent rail, stronger border and shadow, `translateY(-2px)` hover, `:focus-visible` ring, an action pill, and domain accents for FAE, HR, Marketing, and Administration. Disable movement and action styling for unavailable entries.

- [ ] **Step 4: Verify tests and production build**

Run: `npm test -- --run src/pages/AgentUsePage.test.tsx && npm run build`

Expected: all tests pass and Vite build exits 0.

### Task 3: Authorization and production acceptance

**Files:**
- No application file changes; use existing audited `grant_agent_use_scope_v29` maintenance boundary.

**Interfaces:**
- Consumes: canonical eight-Agent Catalog and production identity directory.
- Produces: FAE and Administration all-member visibility; HR and Marketing visibility according to the explicitly selected production grant scope.

- [ ] **Step 1: Query current active grants without exposing identity secrets**

Verify grant counts per canonical Agent and evaluate the effective decisions for the platform owner.

- [ ] **Step 2: Apply only the approved missing grants through the audited maintenance function**

Do not insert rows directly. Keep external workspaces non-dispatchable.

- [ ] **Step 3: Verify the production Catalog projection**

Run the production `AgentUseAuthorization.permitted_catalog_for_user_id()` using the application role and assert ordered visibility separately from dispatchability.

### Task 4: Commit, deploy, and smoke-test

**Files:**
- Modify only the files listed above plus this plan.

**Interfaces:**
- Consumes: green test/build evidence.
- Produces: a versioned Platform release and verified `/agents` behavior.

- [ ] **Step 1: Review the diff and commit**

Commit only the Professional Agent directory changes and this plan.

- [ ] **Step 2: Deploy through the existing Platform release transaction**

Preserve the current Brain provider configuration and enabled feature flags; do not modify or restart FAE.

- [ ] **Step 3: Run production acceptance**

Verify Platform health, both workspace URLs, Catalog visibility, card order in the shipped bundle, Brain dispatch exclusion, and unchanged FAE container identity/start time/restart count.
