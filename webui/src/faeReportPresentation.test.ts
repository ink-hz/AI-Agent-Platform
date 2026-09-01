import { describe, expect, it } from "vitest";

import {
  buildImprovementThemes,
  formatPublishedMetric,
  metricsForChapter,
  reviewCoverage,
} from "./faeReportPresentation";
import type { FaeReportMetric } from "./faeReportTypes";
import { reportFixture } from "./testFixtures/faeReportFixture";


describe("FAE report presentation", () => {
  it("derives review coverage only from the published source counts", () => {
    expect(reviewCoverage(reportFixture)).toEqual({
      numerator: 654,
      denominator: 692,
      ratio: 654 / 692,
    });
  });

  it("keeps an unknown valid metric in its published chapter", () => {
    const report = {
      ...reportFixture,
      metrics: [
        ...reportFixture.metrics,
        {
          metric_id: "usage.new_signal",
          dimension: "usage" as const,
          label: "新增信号",
          value: 3,
          unit: "count" as const,
          numerator: null,
          denominator: null,
          filters: [],
          assumptions: [],
          evidence_artifact_refs: ["metrics.json"],
        },
      ],
    };

    const presented = metricsForChapter(report, "usage");
    expect(presented[presented.length - 1]?.presentation.kind).toBe("generic");
  });

  it("preserves privacy-suppressed distribution values", () => {
    const metric: FaeReportMetric = {
      ...reportFixture.metrics[0],
      value: { Gemini: 326, Oradar: "少于 5" },
      unit: "distribution",
      denominator: 692,
    };

    expect(formatPublishedMetric(metric)).toContain("Oradar 少于 5");
  });

  it("pairs findings and actions only through explicit ids", () => {
    const [theme] = buildImprovementThemes(reportFixture);

    expect(theme.finding.finding_id).toBe("finding-1");
    expect(theme.recommendations.map((item) => item.recommendation_id)).toEqual(["rec-1"]);
  });
});
