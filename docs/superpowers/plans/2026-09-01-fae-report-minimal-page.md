# FAE Report Minimal Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current nested FAE report presentation with one flat management narrative containing exactly four visible dimensions and no visible sub-dimension groups.

**Architecture:** Keep the existing immutable v1 report API and data contract unchanged. Flatten only the WebUI presentation: a concise cover, four chapters, deterministic selection of published metrics, and evidence links that continue to use the existing permission boundary. Report v2 and the business-feedback workbench are separate follow-up plans so this release remains independently deployable.

**Tech Stack:** React 19, TypeScript, Vitest, Vite, existing Platform CSS and report API.

## Global Constraints

- The page has exactly four visible dimensions: 使用情况、业务价值、回答效果、业务反哺.
- Do not render `depth`, `complexity`, `products`, `capabilities`, `quality`, `reliability`, or `other` as visible sections.
- Each dimension contains one conclusion, one compact fact/visual area, and one explanation or evidence area.
- Use only values present in the report payload or report source counts; never hardcode 692, 1492, or any production result.
- Do not add a new privacy, masking, workflow, approval, tag-tree, or mock-data subsystem.
- Keep report v1, report routes, authentication, immutable-version behavior, data-cutoff labels, and evidence authorization unchanged.
- Missing optional metrics remove that visual; they do not create placeholder numbers or inferred values.
- Preserve the distinction between realized value and potential value.
- The worktree is `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/fae-report-business-feedback` on branch `feat/fae-report-business-feedback`.

---

## File Structure

- Modify `webui/src/faeReportPresentation.ts`: remove presentation grouping and expose deterministic metric lookup helpers.
- Modify `webui/src/faeReportPresentation.test.ts`: lock flat ordering and missing-metric behavior.
- Modify `webui/src/components/fae-reports/ExecutiveOutcomeCover.tsx`: render one cover and one four-fact row.
- Modify `webui/src/components/fae-reports/UsageChapter.tsx`: render a flat usage statement, primary facts, and at most one distribution.
- Modify `webui/src/components/fae-reports/BusinessValueChapter.tsx`: render realized and potential values in one comparison area.
- Modify `webui/src/components/fae-reports/AnswerEffectivenessChapter.tsx`: render one compact quality area.
- Modify `webui/src/components/fae-reports/InsightAndImprovementChapter.tsx`: rename to business feedback in user-facing copy and render a flat top-theme list.
- Modify `webui/src/components/fae-reports/ReportChapterNav.tsx`: expose the four approved labels.
- Modify `webui/src/components/fae-reports/ReportChapters.test.tsx`: lock the four flat sections and truthful empty states.
- Modify `webui/src/pages/FaeReportsPage.test.tsx`: verify the report as one four-part narrative.
- Modify `webui/src/styles.css`: remove nested report-group styling and add compact flat layouts with mobile behavior.

---

### Task 1: Flatten the report presentation model

**Files:**
- Modify: `webui/src/faeReportPresentation.ts`
- Test: `webui/src/faeReportPresentation.test.ts`

**Interfaces:**
- Consumes: `FaeAnalysisReport`, `FaeReportMetric`, and `FaeReportDimension` from `webui/src/faeReportTypes.ts`.
- Produces: `metricById(report, metricId)`, `metricsByIds(report, metricIds)`, and a `MetricPresentation` without a `group` property.

- [ ] **Step 1: Write failing tests for flat presentation and exact metric selection**

Add tests that require a presentation without visible grouping and preserve caller-defined metric order:

```ts
import { metricById, metricsByIds } from "./faeReportPresentation";

it("selects published metrics in the caller's narrative order", () => {
  expect(metricsByIds(reportFixture, [
    "quality.reviewed_first_turn_resolution_rate",
    "quality.reviewed_count",
  ]).map((metric) => metric.metric_id)).toEqual([
    "quality.reviewed_first_turn_resolution_rate",
    "quality.reviewed_count",
  ]);
});

it("omits a missing optional metric instead of inventing a value", () => {
  expect(metricById(reportFixture, "usage.not_published")).toBeUndefined();
  expect(metricsByIds(reportFixture, ["usage.not_published"])).toEqual([]);
});

it("does not expose a presentation group", () => {
  expect(metricPresentation(reportFixture.metrics[0])).not.toHaveProperty("group");
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm --prefix webui test -- --run src/faeReportPresentation.test.ts
```

Expected: FAIL because `metricById` and `metricsByIds` do not exist and `MetricPresentation` still contains `group`.

- [ ] **Step 3: Implement the flat presentation helpers**

Change the interface and mappings to this shape:

```ts
export interface MetricPresentation {
  chapter: FaeReportDimension;
  order: number;
  kind: MetricRendererKind;
  managementOutcome?: ManagementOutcome;
  note?: string;
}

export function metricById(
  report: FaeAnalysisReport,
  metricId: string,
): FaeReportMetric | undefined {
  return report.metrics.find((metric) => metric.metric_id === metricId);
}

export function metricsByIds(
  report: FaeAnalysisReport,
  metricIds: readonly string[],
): FaeReportMetric[] {
  return metricIds
    .map((metricId) => metricById(report, metricId))
    .filter((metric): metric is FaeReportMetric => metric !== undefined);
}
```

Remove every `group` entry from `METRIC_PRESENTATION`. Keep chapter, order, renderer kind, outcome, and notes. Unknown valid metrics remain available through `metricsForChapter`, but chapter components decide whether to display them.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
npm --prefix webui test -- --run src/faeReportPresentation.test.ts
```

Expected: all `faeReportPresentation` tests PASS.

- [ ] **Step 5: Commit the presentation boundary**

```bash
git add webui/src/faeReportPresentation.ts webui/src/faeReportPresentation.test.ts
git commit -m "refactor(fae-reports): flatten metric presentation"
```

---

### Task 2: Replace the cover with one conclusion and four facts

**Files:**
- Modify: `webui/src/components/fae-reports/ExecutiveOutcomeCover.tsx`
- Modify: `webui/src/pages/FaeReportsPage.test.tsx`

**Interfaces:**
- Consumes: `metricById`, `formatPublishedMetric`, and `reviewCoverage` from `faeReportPresentation.ts`.
- Produces: `[data-report-cover]` with `.fae-outcome-cover__facts` and four non-invented facts.

- [ ] **Step 1: Write a failing cover test**

Update the existing cover test to require one headline and exactly four facts:

```ts
const cover = container.querySelector("[data-report-cover]");
expect(cover?.textContent).toContain(outcomeReport.summary.headline);
expect(cover?.querySelectorAll(".fae-outcome-cover__facts > article")).toHaveLength(4);
expect(cover?.textContent).toContain("生产会话");
expect(cover?.textContent).toContain("独立复审");
expect(cover?.textContent).toContain("已确认价值");
expect(cover?.textContent).toContain("潜在机会");
expect(cover?.textContent).not.toContain("复杂业务承接");
```

- [ ] **Step 2: Run the page test and verify RED**

Run:

```bash
npm --prefix webui test -- --run src/pages/FaeReportsPage.test.tsx
```

Expected: FAIL because the current cover renders trust, complexity, and value as three nested regions.

- [ ] **Step 3: Implement the concise cover**

Keep `ReportVersionHeader`, then render:

```tsx
<div className="fae-outcome-cover__lead">
  <p className="fae-outcome-kicker">FAE Agent 生产成果</p>
  <h1>{report.summary?.headline ?? report.title}</h1>
  <span>{report.summary?.overview}</span>
</div>
<div className="fae-outcome-cover__facts">
  <article><span>生产会话</span><strong>{NUMBER.format(report.source.session_count)}</strong></article>
  <article><span>独立复审</span><strong>{NUMBER.format(coverage.numerator)} / {NUMBER.format(coverage.denominator)}</strong></article>
  <article data-outcome="realized"><span>已确认价值</span><strong>{publishedOrUnavailable(realized)}</strong></article>
  <article data-outcome="potential"><span>潜在机会</span><strong>{publishedOrUnavailable(potential)}</strong><small>不计入已实现成果</small></article>
</div>
```

`publishedOrUnavailable` returns `formatPublishedMetric(metric)` only when the metric exists; otherwise it returns `暂未发布`. Remove the complexity panel and the separate trust strip.

Define it in the same file as:

```ts
function publishedOrUnavailable(metric: FaeReportMetric | undefined): string {
  return metric ? formatPublishedMetric(metric) : "暂未发布";
}
```

- [ ] **Step 4: Run the page test and verify GREEN**

Run:

```bash
npm --prefix webui test -- --run src/pages/FaeReportsPage.test.tsx
```

Expected: all page tests PASS.

- [ ] **Step 5: Commit the cover**

```bash
git add webui/src/components/fae-reports/ExecutiveOutcomeCover.tsx webui/src/pages/FaeReportsPage.test.tsx
git commit -m "refactor(fae-reports): simplify outcome cover"
```

---

### Task 3: Flatten the four report chapters

**Files:**
- Modify: `webui/src/components/fae-reports/UsageChapter.tsx`
- Modify: `webui/src/components/fae-reports/BusinessValueChapter.tsx`
- Modify: `webui/src/components/fae-reports/AnswerEffectivenessChapter.tsx`
- Modify: `webui/src/components/fae-reports/InsightAndImprovementChapter.tsx`
- Modify: `webui/src/components/fae-reports/ReportChapterNav.tsx`
- Test: `webui/src/components/fae-reports/ReportChapters.test.tsx`
- Test: `webui/src/pages/FaeReportsPage.test.tsx`

**Interfaces:**
- Consumes: `metricById`, `metricsByIds`, `buildImprovementThemes`, `formatPublishedMetric`, and `ReportMetricVisual`.
- Produces: exactly four `[data-dimension]` sections and zero `.fae-outcome-metric-group` elements.

- [ ] **Step 1: Write failing structural tests**

Add these assertions:

```tsx
expect(container.querySelectorAll("[data-dimension]")).toHaveLength(4);
expect(container.querySelectorAll(".fae-outcome-metric-group")).toHaveLength(0);
expect(container.textContent).toContain("业务反哺");
expect(container.textContent).not.toContain("服务规模与深度");
expect(container.textContent).not.toContain("复杂业务承接");
expect(container.textContent).not.toContain("独立复审结果");
expect(container.textContent).not.toContain("可靠性与响应");
```

Change the navigation expectation from `业务洞察与改进` to `业务反哺`.

- [ ] **Step 2: Run chapter and page tests and verify RED**

Run:

```bash
npm --prefix webui test -- --run src/components/fae-reports/ReportChapters.test.tsx src/pages/FaeReportsPage.test.tsx
```

Expected: FAIL because all three current metric chapters render named groups and the fourth chapter uses the old label.

- [ ] **Step 3: Implement the flat usage chapter**

Use one conclusion, one fact grid, and at most one published distribution:

```tsx
const facts = metricsByIds(report, [
  "value.observed_included_sessions",
  "value.observed_included_turns",
  "value.observed_multiturn_sessions",
]);
const distribution = metricById(report, "product.family_counts_public");

return <section className="fae-outcome-chapter" id="report-usage" data-dimension="usage">
  <header className="fae-outcome-chapter__header">
    <p>01 · USAGE</p>
    <h2>使用情况</h2>
    <span>{`${report.source.session_count} 个生产会话完成 ${report.source.turn_count} 轮真实问答。`}</span>
  </header>
  <div className="fae-outcome-chapter__facts">{facts.map((metric) => <ReportMetricVisual
    key={metric.metric_id}
    metric={metric}
    presentation={metricPresentation(metric)}
  />)}</div>
  {distribution && <div className="fae-outcome-chapter__visual"><ReportMetricVisual metric={distribution} presentation={metricPresentation(distribution)} /></div>}
</section>;
```

Keep the conclusion deterministic and based only on `report.source`.

- [ ] **Step 4: Implement the flat business-value chapter**

Render the realized and potential metrics in one `.fae-outcome-value-comparison` without two titled sub-sections. Keep the explicit sentence `潜在机会不计入已实现成果。` Render cases directly below; if empty, keep the truthful `典型案例待业务批准` state.

- [ ] **Step 5: Implement the flat answer-effectiveness chapter**

Select only:

```ts
[
  "quality.reviewed_count",
  "quality.reviewed_fully_resolved_rate",
  "quality.reviewed_first_turn_resolution_rate",
  "feedback.bad_affected_sessions",
]
```

Render them in one fact grid. Do not add quality or reliability headings. If an optional metric is absent, omit it.

- [ ] **Step 6: Implement the flat business-feedback chapter**

Keep `buildImprovementThemes(report)` as the v1 compatibility source. Render a factual conclusion such as `本期报告发布 ${themes.length} 个需要跟进的主题。`, show at most the first eight themes, and keep issue links. Each theme renders only title, description, current recommendation summary, owner role, and the existing evidence link. Remove the separate production-signal metric section, root-cause definition list, internal severity English, and nested recommendation panels.

- [ ] **Step 7: Update the four navigation labels**

Use:

```ts
const CHAPTERS = [
  ["report-usage", "01", "使用情况"],
  ["report-value", "02", "业务价值"],
  ["report-effectiveness", "03", "回答效果"],
  ["report-improvement", "04", "业务反哺"],
] as const;
```

- [ ] **Step 8: Run chapter and page tests and verify GREEN**

Run:

```bash
npm --prefix webui test -- --run src/components/fae-reports/ReportChapters.test.tsx src/pages/FaeReportsPage.test.tsx
```

Expected: all selected tests PASS and no `.fae-outcome-metric-group` is rendered.

- [ ] **Step 9: Commit the four-section narrative**

```bash
git add webui/src/components/fae-reports webui/src/pages/FaeReportsPage.test.tsx
git commit -m "refactor(fae-reports): present four flat outcomes"
```

---

### Task 4: Replace nested styling and verify the production build

**Files:**
- Modify: `webui/src/styles.css`
- Test: `webui/src/components/fae-reports/ReportChapters.test.tsx`
- Test: `webui/src/pages/FaeReportsPage.test.tsx`

**Interfaces:**
- Consumes: `.fae-outcome-cover__facts`, `.fae-outcome-chapter__facts`, `.fae-outcome-chapter__visual`, `.fae-outcome-value-comparison`, and `.fae-improvement-themes` from Tasks 2–3.
- Produces: desktop and mobile layouts without nested group chrome.

- [ ] **Step 1: Add a failing class-governance test**

Read `webui/src/styles.css` in `ReportChapters.test.tsx` and assert the removed selectors are absent:

```ts
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../../styles.css", import.meta.url), "utf8");

expect(css).not.toContain(".fae-outcome-metric-group");
expect(css).not.toContain(".fae-outcome-value-group");
expect(css).toContain(".fae-outcome-chapter__facts");
expect(css).toContain(".fae-outcome-value-comparison");
```

- [ ] **Step 2: Run the chapter test and verify RED**

Run:

```bash
npm --prefix webui test -- --run src/components/fae-reports/ReportChapters.test.tsx
```

Expected: FAIL because nested group selectors still exist and the new flat selectors do not.

- [ ] **Step 3: Implement the flat styles**

Replace nested group styles with these layout responsibilities:

```css
.fae-outcome-cover__facts { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border-top:1px solid #e5ebf2; }
.fae-outcome-cover__facts article { min-width:0; padding:22px 26px; border-right:1px solid #e5ebf2; }
.fae-outcome-chapter__facts { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin-top:24px; border:1px solid #dce5ee; }
.fae-outcome-chapter__visual { margin-top:18px; }
.fae-outcome-value-comparison { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1px; margin-top:24px; background:#dce5ee; border:1px solid #dce5ee; }
```

Remove `.fae-outcome-metric-group` and `.fae-outcome-value-group` rules. Simplify theme cards so recommendations are not nested blue panels. At `max-width: 760px`, make all fact and comparison grids one column. Preserve print rules and focus-visible styles.

- [ ] **Step 4: Run all report frontend tests**

Run:

```bash
npm --prefix webui test -- --run src/components/fae-reports/ReportChapters.test.tsx src/faeReportApi.test.ts src/faeReportPresentation.test.ts src/pages/FaeReportsPage.test.tsx
```

Expected: 4 test files PASS with 0 failures.

- [ ] **Step 5: Run the production build**

Run:

```bash
npm --prefix webui run build
```

Expected: TypeScript and Vite complete with exit code 0.

- [ ] **Step 6: Run the existing report backend regression tests**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest \
  backend/tests/test_fae_report_api.py \
  backend/tests/test_fae_report_contract.py \
  backend/tests/test_fae_report_importer.py \
  backend/tests/test_fae_report_repository.py \
  backend/tests/test_fae_report_service.py \
  backend/tests/test_cloud_fae_report_projection.py -q
```

Expected: 25 tests PASS with 0 failures.

- [ ] **Step 7: Commit the verified page**

```bash
git add webui/src/styles.css webui/src/components/fae-reports/ReportChapters.test.tsx
git commit -m "style(fae-reports): remove nested report chrome"
```

---

## Final Acceptance

- [ ] `git diff --check` exits 0.
- [ ] `rg -n "服务规模与深度|复杂业务承接|独立复审结果|可靠性与响应" webui/src/components/fae-reports` returns no user-facing occurrences.
- [ ] All report frontend tests pass.
- [ ] The WebUI production build passes.
- [ ] All six report backend test modules pass.
- [ ] A reviewer can identify exactly four visible dimensions in the rendered HTML.
- [ ] No backend, report contract, authentication, routing, or production data changed in this phase.
