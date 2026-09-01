import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { FaeReportMetric } from "../../faeReportTypes";
import { metricPresentation } from "../../faeReportPresentation";
import { reportFixture } from "../../testFixtures/faeReportFixture";
import { AnswerEffectivenessChapter } from "./AnswerEffectivenessChapter";
import { BusinessValueChapter } from "./BusinessValueChapter";
import { InsightAndImprovementChapter } from "./InsightAndImprovementChapter";
import { ReportMetricVisual } from "./ReportMetricVisual";
import { UsageChapter } from "./UsageChapter";


const css = readFileSync(new URL("../../styles.css", import.meta.url), "utf8");


function renderMetric(metric: FaeReportMetric): string {
  return renderToStaticMarkup(
    <ReportMetricVisual metric={metric} presentation={metricPresentation(metric)} />,
  );
}


describe("FAE outcome report chapters", () => {
  it("renders ratios with the published fraction and no invented verdict", () => {
    const html = renderMetric({
      metric_id: "quality.reviewed_fully_resolved_rate",
      dimension: "answer_effectiveness",
      label: "完全解决率",
      value: 0.8,
      unit: "ratio",
      numerator: 80,
      denominator: 100,
      filters: [],
      assumptions: [],
      evidence_artifact_refs: ["metrics.json"],
    });

    expect(html).toContain("80.0%");
    expect(html).toContain("80 / 100");
    expect(html).not.toMatch(/达标|不达标|优秀/);
  });

  it("orders numeric distributions and never infers a suppressed value", () => {
    const html = renderMetric({
      metric_id: "product.family_counts_public",
      dimension: "usage",
      label: "高频产品族",
      value: { Femto: 103, Oradar: "少于 5", Gemini: 326 },
      unit: "distribution",
      numerator: null,
      denominator: 692,
      filters: [],
      assumptions: [],
      evidence_artifact_refs: ["metrics.json"],
    });

    expect(html.indexOf("Gemini")).toBeLessThan(html.indexOf("Femto"));
    expect(html.indexOf("Femto")).toBeLessThan(html.indexOf("Oradar"));
    expect(html).toContain("少于 5");
    expect(html).toContain('data-suppressed="true"');
  });

  it("renders explicit latency quantiles in readable seconds", () => {
    const html = renderMetric({
      metric_id: "latency.overall_ms",
      dimension: "answer_effectiveness",
      label: "回答延迟",
      value: { p95: 134400, p50: 47000, p90: 104700 },
      unit: "milliseconds_distribution",
      numerator: null,
      denominator: 1492,
      filters: [],
      assumptions: [],
      evidence_artifact_refs: ["metrics.json"],
    });

    expect(html).toMatch(/P50[^<]*<[^>]+>47\.0 秒/);
    expect(html).toMatch(/P90[^<]*<[^>]+>104\.7 秒/);
    expect(html).toMatch(/P95[^<]*<[^>]+>134\.4 秒/);
  });

  it("compares realized value and potential without nested sub-sections", () => {
    const html = renderToStaticMarkup(<BusinessValueChapter report={reportFixture} />);

    expect(html).toContain("fae-outcome-value-comparison");
    expect(html).not.toContain("fae-outcome-value-group");
    expect(html).toContain("潜在机会不计入已实现成果");
    expect(html).toContain("典型案例待业务批准");
  });

  it("renders one compact business-feedback theme and exposes missing governance linkage", () => {
    const html = renderToStaticMarkup(<InsightAndImprovementChapter report={reportFixture} />);

    expect(html).toContain("业务反哺");
    expect(html.match(/资料缺口/g)).toHaveLength(1);
    expect(html).toContain("建设统一资料入口");
    expect(html).toContain("待建立治理关联");
    expect(html).not.toContain("根因判断");
  });

  it("renders the four chapters without visible metric groups", () => {
    const html = renderToStaticMarkup(<>
      <UsageChapter report={reportFixture} />
      <BusinessValueChapter report={reportFixture} />
      <AnswerEffectivenessChapter report={reportFixture} />
      <InsightAndImprovementChapter report={reportFixture} />
    </>);

    expect(html.match(/data-dimension=/g)).toHaveLength(4);
    expect(html).not.toContain("fae-outcome-metric-group");
    expect(html).not.toContain("服务规模与深度");
    expect(html).not.toContain("独立复审结果");
  });

  it("removes nested group chrome from the report stylesheet", () => {
    expect(css).not.toContain(".fae-outcome-metric-group");
    expect(css).not.toContain(".fae-outcome-value-group");
    expect(css).toContain(".fae-outcome-chapter__facts");
    expect(css).toContain(".fae-outcome-value-comparison");
  });
});
