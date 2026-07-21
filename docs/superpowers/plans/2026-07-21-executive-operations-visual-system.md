# Executive Operations Visual System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Agent Overview to the approved Executive Operations visual weight with larger readable type, stronger contrast, and substantial cards while preserving all product behavior.

**Architecture:** Keep the existing React structure and data flow. Express the new system in the existing stylesheet, add a source-CSS Vitest contract that locks critical color, typography, card, long-name, and responsive requirements, and make no backend or API changes.

**Tech Stack:** React 19, TypeScript, CSS, Vitest, Vite

## Global Constraints

- Keep the white Executive Operations direction; do not introduce a dark dashboard, glassmorphism, neon, Web Fonts, icon libraries, or CSS frameworks.
- No production-visible text may use a CSS `font-size` below `11.5px`.
- Keep Agent names, source-language copy, data, status derivation, polling, API, and Flywheel behavior unchanged.
- Cards must have visible boundaries and shadows without hover.
- Long Agent names must wrap instead of being hidden by tiny type or ellipsis.
- `> 1000px`: four summary columns and three Agent columns; `721–1000px`: two and two; `<= 720px`: one and one.
- Do not create placeholder child pages. The visual tokens in this plan are the baseline for future real child pages.
- Do not modify or restart MetaBot processes; restart only Platform after build.
- Preserve unrelated changes in `backend/app/health/normalizer.py`, `backend/tests/test_health_normalizer.py`, and all existing untracked files.

---

### Task 1: Visual tokens, type scale, summary, and insight surfaces

**Files:**
- Create: `webui/src/styles.test.ts`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: the source stylesheet loaded with Node `readFileSync`
- Produces: tested Executive Operations color tokens, minimum type scale, summary surfaces, insight surfaces, trend values, and ranking scale

- [ ] **Step 1: Write the failing base visual contract**

Create `webui/src/styles.test.ts`:

```typescript
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function rule(selector: string): string {
  const start = styles.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing CSS rule: ${selector}`);
  const end = styles.indexOf("}", start);
  return styles.slice(start, end + 1);
}


describe("Executive Operations visual contract", () => {
  it("uses the approved high-contrast foundation", () => {
    for (const token of [
      "--bg: #edf2f7",
      "--line: #cbd6e2",
      "--line-soft: #dde5ee",
      "--ink: #0f1b2d",
      "--ink-soft: #46566a",
      "--ink-faint: #68778a",
      "--brand: #123f78",
      "--brand-bright: #2468c5",
    ]) expect(styles).toContain(token);
  });

  it("never renders visible text below the approved minimum", () => {
    const sizes = [...styles.matchAll(/font-size:\s*([\d.]+)px/g)].map((match) => Number(match[1]));
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(11.5);
  });

  it("gives summary and insight cards visible resting weight", () => {
    expect(rule(".fleet-summary-card")).toContain("min-height: 150px");
    expect(rule(".fleet-summary-card")).toContain("padding: 24px");
    expect(rule(".fleet-summary-card")).toContain("box-shadow: 0 12px 30px rgba(21, 51, 88, .09)");
    expect(rule(".insight-card")).toContain("min-height: 330px");
    expect(rule(".insight-card")).toContain("padding: 26px");
    expect(rule(".insight-card")).toContain("box-shadow: 0 12px 30px rgba(21, 51, 88, .09)");
  });

  it("keeps chart values readable without hover", () => {
    expect(rule(".trend-value")).toContain("font-size: 12px");
    expect(rule(".trend-value")).toContain("opacity: 1");
    expect(rule(".trend-date")).toContain("font-size: 12px");
    expect(rule(".ranking-main strong")).toContain("font-size: 14px");
    expect(rule(".ranking-main > i")).toContain("height: 7px");
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run from `webui/`:

```bash
npm test -- styles.test.ts
```

Expected: FAIL on old light tokens, `9.5px` minimum text, thin cards, and hover-only chart values.

- [ ] **Step 3: Implement the foundation and primary surfaces**

Update `:root` to the exact tokens in the design spec and apply these exact desktop values:

```css
.topbar-inner { min-height: 72px; }
.brand-name { font-size: 16px; }
.product-nav span { font-size: 14px; }
.readonly-tag { font-size: 12px; }
.eyebrow { font-size: 11.5px; }
.hero h1 { font-size: clamp(34px, 4vw, 44px); }
.hero-sub { font-size: 16px; line-height: 1.65; }
.team-light { font-size: 13px; box-shadow: 0 8px 22px rgba(20, 45, 78, .09); }
.banner { font-size: 14px; }
.section-heading p,
.insight-heading p { font-size: 11.5px; }
.section-heading h2,
.insight-heading h2 { font-size: 20px; }
.section-heading > span,
.insight-heading > span { font-size: 13px; }

.fleet-summary-card {
  min-height: 150px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 12px 30px rgba(21, 51, 88, .09);
}
.fleet-summary-card > span { font-size: 14px; font-weight: 700; }
.fleet-summary-card strong { font-size: 42px; font-weight: 780; }
.fleet-summary-card p { font-size: 13px; }

.insight-card {
  min-height: 330px;
  padding: 26px;
  border: 1px solid var(--line);
  box-shadow: 0 12px 30px rgba(21, 51, 88, .09);
}
.trend-value { color: var(--ink-soft); font-size: 12px; opacity: 1; }
.trend-date { color: var(--ink-faint); font-size: 12px; }
.trend-empty, .ranking-empty { color: var(--ink-soft); font-size: 13px; }
.ranking-index { color: var(--ink-faint); font-size: 11.5px; }
.ranking-avatar { width: 44px; height: 44px; font-size: 12px; }
.ranking-main strong { font-size: 14px; }
.ranking-main span { font-size: 13px; }
.ranking-main > i { height: 7px; }
```

Update the remaining sub-minimum selectors with this exact map:

```css
.eyebrow,
.section-heading p,
.insight-heading p,
.ranking-index,
.fleet-agent-identity p,
.fleet-usage span,
.fleet-agent-meta dt,
.fleet-recent span { font-size: 11.5px; }
```

The other selector-specific sizes are already declared above. After editing, run:

```bash
rg -n 'font-size:\s*(?:[0-9]|10(?:\.[0-9]+)?|11(?:\.[0-4])?)px' src/styles.css
```

Expected: no matches. Do not reduce any existing font size that is already larger than its target.

- [ ] **Step 4: Run the visual contract and all frontend tests**

```bash
npm test -- styles.test.ts
npm test
```

Expected: visual contract and all existing frontend tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add webui/src/styles.css webui/src/styles.test.ts
git commit -m "style: strengthen Platform visual hierarchy"
```

---

### Task 2: Substantial Agent cards and responsive reading

**Files:**
- Modify: `webui/src/styles.test.ts`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: the Task 1 `rule(selector)` CSS test helper
- Produces: substantial Agent cards, visible long names, readable metadata, and one-column mobile summaries

- [ ] **Step 1: Add the failing Agent-card and responsive contract**

Append to the existing `describe` block:

```typescript
it("gives every Agent card substantial resting weight", () => {
  expect(rule(".fleet-agent-card")).toContain("padding: 24px");
  expect(rule(".fleet-agent-card")).toContain("border: 1px solid #c8d4e2");
  expect(rule(".fleet-agent-card")).toContain("box-shadow: 0 14px 34px rgba(20, 51, 89, .11)");
  expect(rule(".fleet-agent-card::before")).toContain("height: 6px");
  expect(rule(".fleet-avatar")).toContain("width: 52px");
  expect(rule(".fleet-avatar")).toContain("height: 52px");
});

it("keeps Agent names and detail rows readable", () => {
  expect(rule(".fleet-agent-identity h3")).toContain("font-size: 19px");
  expect(rule(".fleet-agent-identity h3")).toContain("white-space: normal");
  expect(rule(".fleet-agent-description")).toContain("font-size: 14px");
  expect(rule(".fleet-usage strong")).toContain("font-size: 34px");
  expect(rule(".fleet-agent-meta dd")).toContain("font-size: 13px");
  expect(rule(".fleet-recent p")).toContain("font-size: 13px");
});

it("uses one-column summary and Agent layouts on small screens", () => {
  const mobile = styles.slice(styles.indexOf("@media (max-width: 720px)"));
  expect(mobile).toContain(".fleet-summary-grid { grid-template-columns: 1fr; }");
  expect(mobile).toContain(".fleet-agent-grid { grid-template-columns: 1fr; }");
});
```

- [ ] **Step 2: Run the contract and verify RED**

```bash
npm test -- styles.test.ts
```

Expected: FAIL on `21px` padding, thin border/shadow, `4px` accent, truncated `16.5px` names, and two-column mobile summaries.

- [ ] **Step 3: Implement the Agent and responsive rules**

Apply these exact values:

```css
.fleet-agent-card {
  padding: 24px;
  border: 1px solid #c8d4e2;
  border-radius: 17px;
  box-shadow: 0 14px 34px rgba(20, 51, 89, .11);
}
.fleet-agent-card::before { height: 6px; }
.fleet-agent-card:hover { box-shadow: 0 18px 40px rgba(20, 51, 89, .15); }
.fleet-agent-head { align-items: flex-start; gap: 13px; }
.fleet-avatar { width: 52px; height: 52px; font-size: 13px; }
.fleet-agent-identity p { font-size: 11.5px; }
.fleet-agent-identity h3 {
  overflow: visible;
  font-size: 19px;
  line-height: 1.3;
  white-space: normal;
}
.fleet-state { padding: 6px 10px; font-size: 12px; }
.fleet-state i { width: 7px; height: 7px; }
.fleet-agent-description { min-height: 48px; font-size: 14px; line-height: 1.65; }
.fleet-usage { padding: 18px 0; }
.fleet-usage span { font-size: 11.5px; }
.fleet-usage strong { font-size: 34px; }
.weekly-usage b { font-size: 19px; }
.fleet-agent-meta dt { font-size: 11.5px; }
.fleet-agent-meta dd { font-size: 13px; }
.fleet-recent { padding: 13px; }
.fleet-recent span { font-size: 11.5px; }
.fleet-recent p { font-size: 13px; line-height: 1.5; }

@media (max-width: 720px) {
  .fleet-summary-grid { grid-template-columns: 1fr; }
  .fleet-agent-grid { grid-template-columns: 1fr; }
  .fleet-summary-card { min-height: 132px; padding: 20px; }
  .fleet-summary-card strong { font-size: 36px; }
}
```

Remove the obsolete `@media (max-width: 430px)` summary-column override because the one-column rule now begins at `720px`. Keep reduced-motion behavior unchanged.

- [ ] **Step 4: Run all frontend gates**

```bash
npm test
npm run build
npm audit --omit=dev
```

Expected: all tests PASS, production build succeeds, and production dependency audit reports zero vulnerabilities.

- [ ] **Step 5: Commit Task 2**

```bash
git add webui/src/styles.css webui/src/styles.test.ts
git commit -m "style: reinforce Agent cards and responsive scale"
```

---

### Task 3: Review, merge, deploy, and verify

**Files:**
- Verify: `webui/dist/`
- Verify: LaunchAgent `com.orbbec.ai-agent-platform`
- Verify: `GET http://127.0.0.1:8000/api/fleet/overview`

**Interfaces:**
- Consumes: the built Executive Operations stylesheet and unchanged fleet API
- Produces: the stronger live Agent Overview at `http://127.0.0.1:8000`

- [ ] **Step 1: Request read-only code review**

Review the feature branch diff against this plan and the visual design spec. Critical and Important findings must be fixed before merge; verify fixes with the CSS contract and full frontend suite.

- [ ] **Step 2: Merge the reviewed branch to `master`**

Use the finishing-development-branch workflow. Preserve the user's existing dirty files and delete only the worktree created for this feature after merged tests pass.

- [ ] **Step 3: Verify merged tests and build**

Run from `webui/` on merged `master`:

```bash
npm test
npm run build
npm audit --omit=dev
```

Expected: all frontend tests PASS, build succeeds, and audit reports zero production vulnerabilities.

- [ ] **Step 4: Record MetaBot PIDs and restart only Platform**

Capture the nine `node /Users/agentops/AgentRuntime/metabot/src/index.ts` PIDs, then run:

```bash
launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform
```

Expected: Platform receives a new PID; all nine MetaBot PIDs remain identical.

- [ ] **Step 5: Verify the live product**

```bash
curl -fsS http://127.0.0.1:8000/api/fleet/overview | jq '{summary, names: [.agents[].name]}'
curl -fsS http://127.0.0.1:8000/ | rg 'assets/index-'
```

Expected: nine running Agents, real usage source healthy, source-language names unchanged, and the newest CSS hash served.

- [ ] **Step 6: Verify the visual contract in production output**

```bash
rg -q -- '--bg:#edf2f7' webui/dist/assets/*.css
rg -q 'box-shadow:0 14px 34px rgba(20,51,89,.11)' webui/dist/assets/*.css
rg -q 'font-size:19px' webui/dist/assets/*.css
```

Expected: all three Executive Operations markers exist in minified production CSS.

- [ ] **Step 7: Confirm unrelated work remains untouched**

```bash
git diff --check
git status --short
```

Expected: only the user's pre-existing unrelated modified and untracked files remain; no whitespace errors.
