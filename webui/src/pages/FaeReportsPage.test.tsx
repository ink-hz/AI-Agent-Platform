/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FaeReportApiError, faeReportApi } from "../faeReportApi";
import { FaeReportsPage } from "./FaeReportsPage";


const report = {
  schema_name: "fae.analysis-report",
  schema_version: "1.0.0",
  report_id: "fae-topic-production-through-20260831",
  report_version: 1,
  report_type: "topic",
  status: "ready",
  title: "AI FAE Agent 生产应用成果报告",
  period: { start_at: "2026-07-01T00:00:00Z", end_at: "2026-08-31T00:00:00Z" },
  data_cutoff_at: "2026-08-31T00:00:00Z",
  generated_at: "2026-08-31T08:00:00Z",
  analysis_version: "v5",
  source: { agent_id: "ai-fae-agent", source_kind: "fae", environment: "production", source_snapshot_at: "2026-08-31T00:00:00Z", session_count: 692, turn_count: 1492, feedback_event_count: 12, reviewed_session_count: 654 },
  summary: { headline: "FAE 已形成可持续复审的生产服务能力", overview: "基于真实生产会话。", top_finding_ids: ["finding-1"], top_recommendation_ids: ["rec-1"] },
  metrics: [
    { metric_id: "value.observed_included_sessions", dimension: "usage", label: "累计服务规模", value: 692, unit: "count", numerator: null, denominator: null, filters: ["population=included"], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
    { metric_id: "value.assisted_reviewed_sessions", dimension: "business_value", label: "已复审业务价值", value: 654, unit: "count", numerator: null, denominator: null, filters: [], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
    { metric_id: "quality.reviewed_fully_resolved_rate", dimension: "answer_effectiveness", label: "完全解决率", value: 0.8, unit: "ratio", numerator: 80, denominator: 100, filters: [], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
    { metric_id: "feedback.canonical_issues", dimension: "insights_improvement", label: "治理问题", value: 8, unit: "count", numerator: null, denominator: null, filters: [], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
  ],
  findings: [{ finding_id: "finding-1", dimension: "insights_improvement", severity: "high", title: "资料缺口", description: "识别到高频资料问题", root_cause_hypothesis: "资料分散", impact_scope: "技术支持", metric_ids: ["feedback.canonical_issues"], evidence_refs: [], recommendation_ids: ["rec-1"], linked_issue_ids: [] }],
  recommendations: [{ recommendation_id: "rec-1", dimension: "insights_improvement", priority: "p1", title: "补齐资料", rationale: "减少重复咨询", proposed_action: "建设统一资料入口", owner_role: "FAE", finding_ids: ["finding-1"], success_metric_ids: ["feedback.canonical_issues"] }],
  cases: [], artifact_digests: {}, failure: null,
  publication: { payload_digest: "a".repeat(64), imported_at: "2026-08-31T09:00:00Z" },
  latest_source_sync_at: "2026-09-01T00:00:00Z", currentness: "source_updated",
} as const;

const outcomeReport = {
  ...report,
  metrics: [
    ...report.metrics,
    { metric_id: "value.observed_multiturn_sessions", dimension: "usage", label: "多轮会话", value: 286, unit: "count", numerator: null, denominator: null, filters: [], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
    { metric_id: "value.observed_attachment_sessions", dimension: "usage", label: "图片或附件会话", value: 122, unit: "count", numerator: null, denominator: null, filters: [], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
    { metric_id: "value.observed_non_work_hour_sessions", dimension: "usage", label: "非工作时段会话", value: 156, unit: "count", numerator: null, denominator: null, filters: [], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
    { metric_id: "value.scenario_potential_conversion_sessions", dimension: "business_value", label: "潜在可转化会话", value: 216, unit: "count", numerator: null, denominator: null, filters: [], assumptions: [], evidence_artifact_refs: ["metrics.json"] },
  ],
} as const;


afterEach(() => {
  vi.restoreAllMocks();
  history.replaceState({}, "", "/");
});


describe("FAE reports", () => {
  beforeEach(() => {
    vi.spyOn(faeReportApi, "list").mockResolvedValue([report] as never);
  });

  it("loads the immutable report index and links newer source data to operations", async () => {
    const index = vi.mocked(faeReportApi.list);
    vi.spyOn(faeReportApi, "latest").mockResolvedValue(report as never);
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage />));
    await act(async () => undefined);

    expect(index).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(container.querySelector('a.fae-outcome-currentness-link[href="/admin/fae"]')?.textContent).toContain("查看最新运营数据");
    expect(container.querySelector(`a[href="/admin/fae/reports/${report.report_id}?version=1"]`)).not.toBeNull();
    expect(container.textContent).toContain("数据截止");
    await act(async () => root.unmount()); container.remove();
  });

  it("leads with production scale and separates realized value from potential", async () => {
    vi.spyOn(faeReportApi, "latest").mockResolvedValue(outcomeReport as never);
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage />));
    await act(async () => undefined);

    const cover = container.querySelector("[data-report-cover]");
    expect(cover?.textContent).toContain("FAE Agent 已经在真实生产中形成规模");
    expect(cover?.textContent).toContain("692");
    expect(cover?.textContent).toContain("1,492");
    expect(cover?.textContent).toContain("654 / 692");
    expect(cover?.querySelector("[data-outcome=realized]")?.textContent).toContain("已实现价值");
    expect(cover?.querySelector("[data-outcome=potential]")?.textContent).toContain("不计入已实现成绩");
    expect(cover?.textContent).not.toContain("AI FAE PRODUCTION OUTCOME");
    await act(async () => root.unmount()); container.remove();
  });

  it("renders the real four-dimension result and truthful case state", async () => {
    vi.spyOn(faeReportApi, "latest").mockResolvedValue(report as never);
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage />));
    await act(async () => undefined);

    expect(container.querySelector("[data-report-id]")?.getAttribute("data-report-id")).toBe(report.report_id);
    expect(container.textContent).toContain("692");
    expect(container.textContent).toContain("使用情况");
    expect(container.textContent).toContain("业务价值");
    expect(container.textContent).toContain("回答效果");
    expect(container.textContent).toContain("业务洞察与改进");
    expect(container.textContent).toContain("典型案例待业务批准");
    expect(container.textContent).toContain("数据已有更新");
    await act(async () => root.unmount()); container.remove();
  });

  it("explains when no report has been published yet", async () => {
    vi.spyOn(faeReportApi, "latest").mockRejectedValue(new FaeReportApiError(404));
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage />));
    await act(async () => undefined);

    expect(container.textContent).toContain("尚无已发布的分析报告");
    expect(container.textContent).not.toContain("Platform 暂时无法读取当前页面");
    await act(async () => root.unmount()); container.remove();
  });

  it("renders a report-specific retry state for operational failures", async () => {
    vi.spyOn(faeReportApi, "latest").mockRejectedValue(new FaeReportApiError(503));
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage />));
    await act(async () => undefined);

    expect(container.textContent).toContain("分析报告读取失败");
    expect(container.textContent).toContain("重新尝试");
    await act(async () => root.unmount()); container.remove();
  });

  it("refuses to render a report that fails the published contract", async () => {
    vi.spyOn(faeReportApi, "latest").mockRejectedValue(new Error("FAE report response contract invalid"));
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage />));
    await act(async () => undefined);

    expect(container.textContent).toContain("报告内容未通过读取校验");
    expect(container.textContent).not.toContain("分析报告读取失败");
    await act(async () => root.unmount()); container.remove();
  });

  it.each([
    [401, "需要登录后查看分析报告"],
    [403, "当前账号无权查看分析报告"],
  ])("distinguishes report access status %i from an operational failure", async (status, message) => {
    vi.spyOn(faeReportApi, "latest").mockRejectedValue(new FaeReportApiError(status));
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage />));
    await act(async () => undefined);

    expect(container.textContent).toContain(message);
    expect(container.textContent).not.toContain("报告数据暂时无法读取");
    expect(container.textContent).not.toContain("重新尝试");
    await act(async () => root.unmount()); container.remove();
  });

  it("loads the selected immutable version from the query string", async () => {
    history.replaceState({}, "", `/admin/fae/reports/${report.report_id}?version=2`);
    const detail = vi.spyOn(faeReportApi, "detail").mockResolvedValue({
      ...report,
      report_version: 2,
    } as never);
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage reportId={report.report_id} />));
    await act(async () => undefined);

    expect(detail).toHaveBeenCalledWith(report.report_id, 2, expect.any(AbortSignal));
    await act(async () => root.unmount()); container.remove();
  });

  it("rejects an invalid report version without issuing a detail request", async () => {
    history.replaceState({}, "", `/admin/fae/reports/${report.report_id}?version=0`);
    const detail = vi.spyOn(faeReportApi, "detail");
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<FaeReportsPage reportId={report.report_id} />));
    await act(async () => undefined);

    expect(detail).not.toHaveBeenCalled();
    expect(container.textContent).toContain("报告版本无效");
    await act(async () => root.unmount()); container.remove();
  });
});
