# FAE Agent Production Outcome Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat FAE report card wall with an evidence-backed production outcome reader that tells management what was achieved, preserves the four approved analysis dimensions, and supports immutable report versions without using mock data.

**Architecture:** Keep `fae.analysis-report` v1 as the source of truth. Add a pure frontend presentation layer keyed by stable metric IDs, lightweight backend report summaries for the version index, and dedicated React chapter components for each analysis dimension. This plan deliberately keeps the current Owner/Admin authorization boundary; the approved three-tier projection and full Evidence Resolver remain a separate security plan so the reader can ship without widening access.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, PostgreSQL repository abstraction, React 19, TypeScript 5.6, Vitest 3, CSS.

## Global Constraints

- Use only structured `fae.analysis-report` data; do not embed `report.html`, private analysis files, raw conversations or production numbers in frontend code.
- Keep exactly four analysis chapters: `usage`, `business_value`, `answer_effectiveness`, `insights_improvement`.
- Keep realized value visually and semantically separate from conversion potential; potential must say it is not realized value.
- Do not infer approved cases from evidence; only render records with `business_case_approved=true`.
- Do not invent quality thresholds, traffic-light scores, savings, revenue or headcount replacement claims.
- Preserve privacy suppression strings such as `少于 5` and never infer their hidden values.
- Unknown valid metric IDs must render in a generic published-metrics section instead of disappearing.
- Findings and recommendations may be paired only through `recommendation_ids` and `finding_ids`, never by title similarity.
- Keep the current Platform Owner/Admin authorization boundary in this plan; do not broaden access to `member` or `management_viewer`.
- Do not modify AI ADMIN `/office/*`, FAE public routes, FAE runtime, FAE database, Nginx or deployment ownership.
- Follow strict TDD: every production change is preceded by a failing focused test.

---

### Task 1: Build the Pure Report Presentation Model

**Files:**
- Create: `webui/src/faeReportPresentation.ts`
- Create: `webui/src/faeReportPresentation.test.ts`
- Modify: `webui/src/faeReportTypes.ts`

**Interfaces:**
- Consumes: `FaeAnalysisReport`, `FaeReportMetric`, `FaeReportFinding`, `FaeReportRecommendation`.
- Produces: `metricPresentation(metric): MetricPresentation`, `metricsForChapter(report, dimension): PresentedMetric[]`, `buildImprovementThemes(report): ImprovementTheme[]`, `reviewCoverage(report): RatioValue`, and `formatPublishedMetric(metric): string`.

- [ ] **Step 1: Write failing presentation tests**

```typescript
import { describe, expect, it } from "vitest";
import {
  buildImprovementThemes,
  formatPublishedMetric,
  metricsForChapter,
  reviewCoverage,
} from "./faeReportPresentation";
import { reportFixture } from "./testFixtures/faeReportFixture";

describe("FAE report presentation", () => {
  it("derives review coverage only from the published source counts", () => {
    expect(reviewCoverage(reportFixture)).toEqual({ numerator: 654, denominator: 692, ratio: 654 / 692 });
  });

  it("keeps an unknown valid metric in its published chapter", () => {
    const report = { ...reportFixture, metrics: [...reportFixture.metrics, {
      metric_id: "usage.new_signal", dimension: "usage", label: "新增信号", value: 3,
      unit: "count", numerator: null, denominator: null, filters: [], assumptions: [],
      evidence_artifact_refs: ["metrics.json"],
    }] };
    expect(metricsForChapter(report, "usage").at(-1)?.presentation.kind).toBe("generic");
  });

  it("preserves privacy-suppressed distribution values", () => {
    expect(formatPublishedMetric({
      ...reportFixture.metrics[0], value: { Gemini: 326, Oradar: "少于 5" },
      unit: "distribution", denominator: 692,
    })).toContain("Oradar 少于 5");
  });

  it("pairs findings and actions only through explicit ids", () => {
    const [theme] = buildImprovementThemes(reportFixture);
    expect(theme.finding.finding_id).toBe("finding-1");
    expect(theme.recommendations.map((item) => item.recommendation_id)).toEqual(["rec-1"]);
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `cd webui && npm test -- faeReportPresentation.test.ts`

Expected: FAIL because `faeReportPresentation.ts` and the fixture do not exist.

- [ ] **Step 3: Add a reusable strict report fixture**

Create `webui/src/testFixtures/faeReportFixture.ts` with a complete ready report containing all four dimensions, explicit numerator/denominator fields, one distribution containing `少于 5`, one linked Finding/Recommendation pair and no approved cases. Use small synthetic values in tests; do not copy the private production report.

- [ ] **Step 4: Implement the metric registry and pure transformations**

```typescript
export type MetricRendererKind = "number" | "ratio" | "ranked_distribution" | "latency_quantiles" | "generic";

export interface MetricPresentation {
  chapter: FaeReportDimension;
  group: string;
  order: number;
  kind: MetricRendererKind;
  managementOutcome?: "scale" | "complexity" | "realized" | "potential";
  note?: string;
}

const METRIC_PRESENTATION: Readonly<Record<string, Omit<MetricPresentation, "chapter"> & { chapter: FaeReportDimension }>> = {
  "value.observed_included_sessions": { chapter: "usage", group: "depth", order: 10, kind: "number", managementOutcome: "scale" },
  "value.observed_included_turns": { chapter: "usage", group: "depth", order: 20, kind: "number" },
  "value.observed_multiturn_sessions": { chapter: "usage", group: "complexity", order: 30, kind: "number", managementOutcome: "complexity" },
  "value.observed_attachment_sessions": { chapter: "usage", group: "complexity", order: 40, kind: "number" },
  "value.observed_non_work_hour_sessions": { chapter: "usage", group: "complexity", order: 50, kind: "number" },
  "product.family_counts_public": { chapter: "usage", group: "products", order: 60, kind: "ranked_distribution" },
  "demand.intent_capability_counts_public": { chapter: "usage", group: "capabilities", order: 70, kind: "ranked_distribution", note: "同一会话可包含多个需求标签，以下为非互斥统计。" },
  "value.assisted_reviewed_sessions": { chapter: "business_value", group: "realized", order: 10, kind: "number", managementOutcome: "realized" },
  "value.scenario_potential_conversion_sessions": { chapter: "business_value", group: "potential", order: 20, kind: "number", managementOutcome: "potential" },
  "quality.reviewed_count": { chapter: "answer_effectiveness", group: "quality", order: 10, kind: "number" },
  "quality.reviewed_fully_resolved_rate": { chapter: "answer_effectiveness", group: "quality", order: 20, kind: "ratio" },
  "quality.reviewed_first_turn_resolution_rate": { chapter: "answer_effectiveness", group: "quality", order: 30, kind: "ratio" },
  "quality.reviewed_multiturn_convergence_rate": { chapter: "answer_effectiveness", group: "quality", order: 40, kind: "ratio" },
  "reliability.fallback_turn_rate": { chapter: "answer_effectiveness", group: "reliability", order: 70, kind: "ratio" },
  "latency.overall_ms": { chapter: "answer_effectiveness", group: "reliability", order: 80, kind: "latency_quantiles" },
  "feedback.canonical_issues": { chapter: "insights_improvement", group: "governance", order: 10, kind: "number" },
  "product.signal_counts_public": { chapter: "insights_improvement", group: "signals", order: 20, kind: "ranked_distribution" },
  "product.scenario_counts_public": { chapter: "insights_improvement", group: "scenarios", order: 30, kind: "ranked_distribution" },
  "workflow.failure_layer_counts_public": { chapter: "insights_improvement", group: "failures", order: 40, kind: "ranked_distribution" },
};
```

Implement generic fallback ordering after all known metrics, safe number/ratio/duration formatting, explicit relation lookup, and stable priority ordering `p0`, `p1`, `p2`, `p3`.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `cd webui && npm test -- faeReportPresentation.test.ts`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add webui/src/faeReportPresentation.ts webui/src/faeReportPresentation.test.ts webui/src/faeReportTypes.ts webui/src/testFixtures/faeReportFixture.ts
git commit -m "feat(fae-reports): add outcome presentation model"
```

---

### Task 2: Return Lightweight Report Summaries and Preserve Ready Fallback

**Files:**
- Modify: `backend/app/fae_reports/service.py`
- Modify: `backend/app/fae_reports/routes.py`
- Modify: `backend/tests/test_fae_report_service.py`
- Modify: `backend/tests/test_fae_report_api.py`

**Interfaces:**
- Produces: `FaeReportService.list_summaries(status: str | None = None) -> list[dict[str, object]]`.
- Summary fields: `report_id`, `report_version`, `report_type`, `status`, `title`, `period`, `data_cutoff_at`, `generated_at`, `analysis_version`, `failure`, `publication`, `latest_source_sync_at`, `currentness`.
- Preserves: `latest()` returns the newest ready report even when a newer failed attempt exists.

- [ ] **Step 1: Write failing service tests**

```python
def test_list_summaries_excludes_report_body():
    [summary] = FaeReportService(Repository()).list_summaries()
    assert summary["report_id"] == "fae-weekly-2026-w35"
    assert set(summary).isdisjoint({"metrics", "findings", "recommendations", "cases", "artifact_digests"})


def test_latest_keeps_newest_ready_when_newer_attempt_failed():
    repository = Repository.with_ready_and_newer_failed()
    result = FaeReportService(repository).latest()
    assert result is not None
    assert result["status"] == "ready"
```

- [ ] **Step 2: Run service tests and verify RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_fae_report_service.py`

Expected: FAIL because `list_summaries` and the two-report fixture helper do not exist.

- [ ] **Step 3: Implement summary projection**

```python
SUMMARY_FIELDS = (
    "report_id", "report_version", "report_type", "status", "title", "period",
    "data_cutoff_at", "generated_at", "analysis_version", "failure", "publication",
    "latest_source_sync_at", "currentness",
)

def list_summaries(self, status: str | None = None) -> list[dict[str, Any]]:
    return [
        {key: document[key] for key in SUMMARY_FIELDS}
        for document in (self._decorate(value) for value in self._repository.list_reports(status=status))
    ]
```

Keep `list()` temporarily as a compatibility alias only if another caller exists; otherwise replace the route caller directly.

- [ ] **Step 4: Add failing API contract tests**

```python
def test_report_index_is_lightweight_and_version_detail_is_exact():
    client = TestClient(app(Role.PLATFORM_OWNER))
    response = client.get("/api/admin/fae/reports")
    assert response.status_code == 200
    assert "metrics" not in response.json()[0]
    assert client.get("/api/admin/fae/reports/fae-topic-production-through-20260831?version=2").json()["report_version"] == 2


def test_report_index_keeps_current_owner_admin_boundary():
    assert TestClient(app(Role.MANAGEMENT_VIEWER)).get("/api/admin/fae/reports").status_code == 403
    assert TestClient(app(Role.MEMBER)).get("/api/admin/fae/reports").status_code == 403
```

- [ ] **Step 5: Point the GET collection route at summaries and verify GREEN**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_fae_report_service.py tests/test_fae_report_api.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/fae_reports/service.py backend/app/fae_reports/routes.py backend/tests/test_fae_report_service.py backend/tests/test_fae_report_api.py
git commit -m "feat(fae-reports): expose lightweight report index"
```

---

### Task 3: Add Strict Report Index and Versioned Client Reads

**Files:**
- Create: `webui/src/faeReportApi.test.ts`
- Modify: `webui/src/faeReportApi.ts`
- Modify: `webui/src/faeReportTypes.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/pages/FaeReportsPage.test.tsx`

**Interfaces:**
- Produces: `FaeReportSummary`, `parseFaeReportSummaryList`, `faeReportApi.list(signal?)`, `faeReportApi.detail(reportId, version?, signal?)`.
- URL contract: detail pages use `/admin/fae/reports/{report_id}?version=N`; omitted version means newest version for that ID.

- [ ] **Step 1: Write failing client parsing tests**

```typescript
it("parses a lightweight report index without accepting report bodies", async () => {
  mockJson([{ report_id: "fae-topic-one", report_version: 2, report_type: "topic", status: "ready",
    title: "成果报告", period: { start_at: ISO_A, end_at: ISO_B }, data_cutoff_at: ISO_B,
    generated_at: ISO_C, analysis_version: "v5", failure: null, publication: { payload_digest: DIGEST, imported_at: ISO_C },
    latest_source_sync_at: ISO_C, currentness: "source_updated" }]);
  const result = await faeReportApi.list();
  expect(result[0].report_version).toBe(2);
});

it("sends an exact positive version when requested", async () => {
  mockJson(reportFixture);
  await faeReportApi.detail(reportFixture.report_id, 2);
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("?version=2"), expect.anything());
});
```

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- faeReportApi.test.ts`

Expected: FAIL because list parsing and versioned detail are absent.

- [ ] **Step 3: Implement strict summary and detail parsing**

Reject unknown top-level report response fields that could carry raw `question`, `answer`, `mobile`, `sender_user_id` or attachment content. Validate every metric, finding, recommendation and case shape used by the reader instead of casting the raw object after shallow checks.

```typescript
export const faeReportApi = {
  list: (signal?: AbortSignal) => getSummaryList("/api/admin/fae/reports", signal),
  latest: (signal?: AbortSignal) => getReport("/api/admin/fae/reports/latest", signal),
  detail: (reportId: string, version?: number, signal?: AbortSignal) => {
    const query = version === undefined ? "" : `?version=${encodeURIComponent(String(version))}`;
    return getReport(`/api/admin/fae/reports/${encodeURIComponent(reportId)}${query}`, signal);
  },
};
```

- [ ] **Step 4: Add failing page version-selection test**

```typescript
it("loads the selected immutable version from the query string", async () => {
  history.replaceState({}, "", `/admin/fae/reports/${reportFixture.report_id}?version=2`);
  vi.spyOn(faeReportApi, "detail").mockResolvedValue({ ...reportFixture, report_version: 2 });
  await renderPage(<FaeReportsPage reportId={reportFixture.report_id} />);
  expect(faeReportApi.detail).toHaveBeenCalledWith(reportFixture.report_id, 2, expect.any(AbortSignal));
  expect(screenText()).toContain("版本 2");
});
```

- [ ] **Step 5: Implement positive-integer query parsing and verify GREEN**

Invalid, repeated or non-positive `version` values render a safe report-version error and do not call the API. Preserve the canonical path in `router.ts`; the version remains a query parameter.

Run: `cd webui && npm test -- faeReportApi.test.ts FaeReportsPage.test.tsx router.test.ts`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add webui/src/faeReportApi.ts webui/src/faeReportApi.test.ts webui/src/faeReportTypes.ts webui/src/pages/FaeReportsPage.test.tsx webui/src/router.test.ts
git commit -m "feat(fae-reports): support immutable report versions"
```

---

### Task 4: Build the Executive Outcome Cover

**Files:**
- Create: `webui/src/components/fae-reports/ExecutiveOutcomeCover.tsx`
- Create: `webui/src/components/fae-reports/ReportVersionHeader.tsx`
- Create: `webui/src/components/fae-reports/ReportChapterNav.tsx`
- Modify: `webui/src/pages/FaeReportsPage.tsx`
- Modify: `webui/src/pages/FaeReportsPage.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: ready `FaeAnalysisReport`, `reviewCoverage`, and registered management outcomes.
- Produces: semantic sections with IDs `report-cover`, `report-usage`, `report-value`, `report-effectiveness`, `report-improvement`.

- [ ] **Step 1: Write failing cover tests**

```typescript
it("leads with production scale and separates realized value from potential", async () => {
  await renderReadyReport(reportFixture);
  const cover = container.querySelector("[data-report-cover]")!;
  expect(cover.textContent).toContain("FAE Agent 已经在真实生产中形成规模");
  expect(cover.textContent).toContain("692");
  expect(cover.textContent).toContain("1,492");
  expect(cover.textContent).toContain("654 / 692");
  expect(cover.querySelector("[data-outcome=realized]")?.textContent).toContain("已实现价值");
  expect(cover.querySelector("[data-outcome=potential]")?.textContent).toContain("不计入已实现成绩");
  expect(cover.textContent).not.toContain("AI FAE PRODUCTION OUTCOME");
});
```

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- FaeReportsPage.test.tsx`

Expected: FAIL because the existing hero uses the old title and equal-weight cards.

- [ ] **Step 3: Implement the header, cover and chapter navigation**

Use semantic `header`, `nav`, `section`, `dl` and anchor elements. The cover receives values through selectors, not literal production numbers. The review coverage text renders numerator, denominator and one-decimal percent. Missing optional complexity metrics render “当前报告未发布该项口径” without hiding the section.

- [ ] **Step 4: Add focused CSS and verify GREEN**

Use an asymmetric cover: narrative and scale on the left, review trust block on the right, then independent realized/potential bands. Avoid decorative gradients and card-wall shadows. Add `scroll-margin-top` to chapter anchors and visible `:focus-visible` styles.

Run: `cd webui && npm test -- FaeReportsPage.test.tsx && npm run build`

Expected: tests PASS and TypeScript/Vite build succeeds.

- [ ] **Step 5: Commit**

```bash
git add webui/src/components/fae-reports/ExecutiveOutcomeCover.tsx webui/src/components/fae-reports/ReportVersionHeader.tsx webui/src/components/fae-reports/ReportChapterNav.tsx webui/src/pages/FaeReportsPage.tsx webui/src/pages/FaeReportsPage.test.tsx webui/src/styles.css
git commit -m "feat(fae-reports): add executive outcome cover"
```

---

### Task 5: Render the Four Specialized Analysis Chapters

**Files:**
- Create: `webui/src/components/fae-reports/ReportMetricVisual.tsx`
- Create: `webui/src/components/fae-reports/ReportMethodology.tsx`
- Create: `webui/src/components/fae-reports/UsageChapter.tsx`
- Create: `webui/src/components/fae-reports/BusinessValueChapter.tsx`
- Create: `webui/src/components/fae-reports/AnswerEffectivenessChapter.tsx`
- Create: `webui/src/components/fae-reports/InsightAndImprovementChapter.tsx`
- Create: `webui/src/components/fae-reports/ReportChapters.test.tsx`
- Modify: `webui/src/pages/FaeReportsPage.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- `ReportMetricVisual({ metric, presentation })` renders number, ratio, ranked distribution, latency quantiles or generic fallback.
- `ReportMethodology({ metric })` exposes denominator, numerator, filters, assumptions and artifact labels through an accessible `<details>` element.
- Each chapter consumes only metrics selected for its own fixed dimension.

- [ ] **Step 1: Write failing renderer tests**

```typescript
it("renders ratios with the published fraction and no invented verdict", () => {
  renderMetric(ratioMetric);
  expect(text()).toContain("80.0%");
  expect(text()).toContain("80 / 100");
  expect(text()).not.toMatch(/达标|不达标|优秀/);
});

it("orders numeric distribution entries while preserving privacy suppression", () => {
  renderMetric(distributionMetric);
  expect(labels()).toEqual(["Gemini", "Femto", "Oradar"]);
  expect(text()).toContain("少于 5");
});

it("renders p50 p90 and p95 as explicit latency quantiles", () => {
  renderMetric(latencyMetric);
  expect(text()).toMatch(/P50.*47\.0 秒/);
  expect(text()).toMatch(/P90.*104\.7 秒/);
  expect(text()).toMatch(/P95.*134\.4 秒/);
});
```

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- ReportChapters.test.tsx`

Expected: FAIL because the specialized chapter components do not exist.

- [ ] **Step 3: Implement metric primitives and methodology**

Rank only numeric distribution entries by descending value. Append privacy-suppressed entries after numeric entries in source-key order and render them without proportional bars. Ratios use published `value`, `numerator` and `denominator`; a missing ratio fraction is a contract error. Latency converts milliseconds to readable seconds while retaining the quantile labels.

- [ ] **Step 4: Implement Usage and Business Value chapters**

Usage groups depth, complexity, products and capabilities. Business Value has separate `data-value-kind="realized"` and `data-value-kind="potential"` containers. Approved cases appear inside Business Value; an empty list renders one restrained line.

- [ ] **Step 5: Implement Effectiveness and Improvement chapters**

Effectiveness separates quality from reliability. Improvement renders three distributions followed by `ImprovementTheme` records ordered by priority. A theme with linked issues renders `/admin/fae/issues/{issue_id}` links; a theme without links says “待建立治理关联”.

- [ ] **Step 6: Verify GREEN**

Run: `cd webui && npm test -- ReportChapters.test.tsx FaeReportsPage.test.tsx`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add webui/src/components/fae-reports webui/src/pages/FaeReportsPage.tsx webui/src/styles.css
git commit -m "feat(fae-reports): add four outcome report chapters"
```

---

### Task 6: Integrate Index, Error States, Responsive Reading and Print

**Files:**
- Modify: `webui/src/pages/FaeReportsPage.tsx`
- Modify: `webui/src/pages/FaeReportsPage.test.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`

**Interfaces:**
- The collection route loads index + latest ready; the detail route loads index + requested version.
- Currentness links to `/admin/fae`; frozen report numbers never merge with overview values.

- [ ] **Step 1: Write failing integration tests**

```typescript
it("keeps the last readable report visible when the source has newer data", async () => {
  await renderReadyReport({ ...reportFixture, currentness: "source_updated" });
  expect(text()).toContain("生产数据已有更新");
  expect(link("查看最新运营数据")).toHaveAttribute("href", "/admin/fae");
  expect(text()).toContain("数据截止");
});

it("keeps report navigation visible during a 503", async () => {
  vi.spyOn(faeReportApi, "latest").mockRejectedValue(new FaeReportApiError(503));
  await renderPage();
  expect(text()).toContain("FAE 工作台");
  expect(text()).toContain("分析报告读取失败");
  expect(button("重新尝试")).toBeVisible();
});

it("does not broaden report access copy into an authorization promise", async () => {
  vi.spyOn(faeReportApi, "latest").mockRejectedValue(new FaeReportApiError(403));
  await renderPage();
  expect(text()).toContain("当前账号无权查看分析报告");
  expect(text()).not.toContain("所有员工");
});
```

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- FaeReportsPage.test.tsx styles.test.ts documentTitle.test.tsx`

Expected: FAIL because index/version status, print rules and new product title are absent.

- [ ] **Step 3: Implement page orchestration and truthful states**

The top-level page owns loading, retry, selected version and error mapping. Abort stale requests. A failed report renders only its sanitized failure payload. A malformed report renders “报告内容未通过读取校验”. Keep `FaeWorkbenchShell currentSection="reports"` mounted for every state.

- [ ] **Step 4: Implement responsive, reduced-motion and print CSS**

At widths above 1100px use a content column plus 240px sticky chapter rail. Below 1100px use one column and horizontal chapter links. Below 560px stack outcome blocks and keep distribution labels readable. Under `@media print`, hide `.app-shell`, `.fae-workbench__sidebar`, interactive version controls and buttons; retain report title, dates, values, methodology and chapter headings. Under reduced motion, disable smooth anchor scrolling.

- [ ] **Step 5: Update document title and verify GREEN**

Use `FAE Agent 生产成果 · Orbbec Agent Platform` for both report routes.

Run:

```bash
cd webui
npm test -- faeReportPresentation.test.ts faeReportApi.test.ts ReportChapters.test.tsx FaeReportsPage.test.tsx router.test.ts styles.test.ts documentTitle.test.tsx
npm run build
```

Expected: all selected tests PASS and production build succeeds.

- [ ] **Step 6: Commit**

```bash
git add webui/src/pages/FaeReportsPage.tsx webui/src/pages/FaeReportsPage.test.tsx webui/src/styles.css webui/src/styles.test.ts webui/src/documentTitle.ts webui/src/documentTitle.test.tsx
git commit -m "feat(fae-reports): complete production outcome reader"
```

---

### Task 7: Full Verification and Release-Safe Handoff

**Files:**
- Modify only if verification exposes a regression in files already changed by Tasks 1–6.

**Interfaces:**
- Produces a tested local feature branch ready for review and local merge.

- [ ] **Step 1: Run the complete backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`

Expected: zero failed tests.

- [ ] **Step 2: Run the complete frontend suite**

Run: `cd webui && npm test`

Expected: zero failed test files and zero failed tests.

- [ ] **Step 3: Build the production frontend**

Run: `cd webui && npm run build`

Expected: TypeScript and Vite build succeed.

- [ ] **Step 4: Run repository and diff checks**

Run:

```bash
git diff --check
git status --short
git log --oneline --decorate -8
```

Expected: no whitespace errors; only intentional feature commits and no user-owned untracked files inside the isolated worktree.

- [ ] **Step 5: Inspect the page against the real report contract**

Use the production-compatible `platform_report_v1.json` only as runtime input to the local API/import path. Verify that the browser displays 692 Sessions, 1492 Turns, 654 reviewed Sessions, 179 realized-value Sessions, 216 potential Sessions, 21 metrics, 8 improvement themes and no fabricated cases. Do not copy this private artifact into WebUI fixtures or static assets.

- [ ] **Step 6: Verify non-target route invariants before deployment**

Record current responses for `/office/`, `/office/?view=services` and `https://fae.orbbec.com.cn/`. The feature diff must contain no Nginx, AI ADMIN, FAE runtime or public FAE files.

- [ ] **Step 7: Request code review before merge**

Use `superpowers:requesting-code-review` against the implementation range. Resolve Critical and Important findings with new failing tests before merging.
