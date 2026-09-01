import type {
  FaeAnalysisReport,
  FaeReportDimension,
  FaeReportFinding,
  FaeReportMetric,
  FaeReportRecommendation,
} from "./faeReportTypes";


export type MetricRendererKind = "number" | "ratio" | "ranked_distribution" | "latency_quantiles" | "generic";
export type ManagementOutcome = "scale" | "complexity" | "realized" | "potential";

export interface MetricPresentation {
  chapter: FaeReportDimension;
  order: number;
  kind: MetricRendererKind;
  managementOutcome?: ManagementOutcome;
  note?: string;
}

export interface PresentedMetric {
  metric: FaeReportMetric;
  presentation: MetricPresentation;
}

export interface RatioValue {
  numerator: number;
  denominator: number;
  ratio: number;
}

export interface ImprovementTheme {
  finding: FaeReportFinding;
  recommendations: FaeReportRecommendation[];
}


const METRIC_PRESENTATION: Readonly<Record<string, MetricPresentation>> = {
  "value.observed_included_sessions": { chapter: "usage", order: 10, kind: "number", managementOutcome: "scale" },
  "value.observed_included_turns": { chapter: "usage", order: 20, kind: "number" },
  "value.observed_multiturn_sessions": { chapter: "usage", order: 30, kind: "number", managementOutcome: "complexity" },
  "value.observed_attachment_sessions": { chapter: "usage", order: 40, kind: "number" },
  "value.observed_non_work_hour_sessions": { chapter: "usage", order: 50, kind: "number" },
  "product.family_counts_public": { chapter: "usage", order: 60, kind: "ranked_distribution" },
  "demand.intent_capability_counts_public": {
    chapter: "usage",
    order: 70,
    kind: "ranked_distribution",
    note: "同一会话可包含多个需求标签，以下为非互斥统计。",
  },
  "value.assisted_reviewed_sessions": { chapter: "business_value", order: 10, kind: "number", managementOutcome: "realized" },
  "value.scenario_potential_conversion_sessions": { chapter: "business_value", order: 20, kind: "number", managementOutcome: "potential" },
  "quality.reviewed_count": { chapter: "answer_effectiveness", order: 10, kind: "number" },
  "quality.reviewed_fully_resolved_rate": { chapter: "answer_effectiveness", order: 20, kind: "ratio" },
  "quality.reviewed_first_turn_resolution_rate": { chapter: "answer_effectiveness", order: 30, kind: "ratio" },
  "quality.reviewed_multiturn_convergence_rate": { chapter: "answer_effectiveness", order: 40, kind: "ratio" },
  "feedback.bad_affected_sessions": { chapter: "answer_effectiveness", order: 50, kind: "number" },
  "feedback.bad_affected_turns": { chapter: "answer_effectiveness", order: 60, kind: "number" },
  "reliability.fallback_turn_rate": { chapter: "answer_effectiveness", order: 70, kind: "ratio" },
  "latency.overall_ms": { chapter: "answer_effectiveness", order: 80, kind: "latency_quantiles" },
  "feedback.canonical_issues": { chapter: "insights_improvement", order: 10, kind: "number" },
  "product.signal_counts_public": { chapter: "insights_improvement", order: 20, kind: "ranked_distribution" },
  "product.scenario_counts_public": { chapter: "insights_improvement", order: 30, kind: "ranked_distribution" },
  "workflow.failure_layer_counts_public": { chapter: "insights_improvement", order: 40, kind: "ranked_distribution" },
};

const PRIORITY_ORDER: Readonly<Record<string, number>> = { p0: 0, p1: 1, p2: 2, p3: 3 };
const NUMBER = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });


export function metricPresentation(metric: FaeReportMetric): MetricPresentation {
  return METRIC_PRESENTATION[metric.metric_id] ?? {
    chapter: metric.dimension,
    order: Number.MAX_SAFE_INTEGER,
    kind: "generic",
  };
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


export function metricsForChapter(report: FaeAnalysisReport, dimension: FaeReportDimension): PresentedMetric[] {
  return report.metrics
    .filter((metric) => metric.dimension === dimension)
    .map((metric) => ({ metric, presentation: metricPresentation(metric) }))
    .sort((left, right) => left.presentation.order - right.presentation.order
      || left.metric.metric_id.localeCompare(right.metric.metric_id));
}


export function reviewCoverage(report: FaeAnalysisReport): RatioValue {
  const denominator = report.source.session_count;
  const numerator = report.source.reviewed_session_count;
  return { numerator, denominator, ratio: denominator === 0 ? 0 : numerator / denominator };
}


function formatNumber(value: number): string {
  return NUMBER.format(value);
}


export function formatPublishedMetric(metric: FaeReportMetric): string {
  if (typeof metric.value === "object") {
    return Object.entries(metric.value)
      .map(([key, value]) => `${key} ${typeof value === "number" ? formatNumber(value) : value}`)
      .join(" · ");
  }
  if (metric.unit === "ratio") return `${(metric.value * 100).toFixed(1)}%`;
  if (metric.unit === "percent") return `${metric.value.toFixed(1)}%`;
  if (metric.unit === "milliseconds") return `${formatNumber(metric.value / 1000)} 秒`;
  if (metric.unit === "seconds") return `${formatNumber(metric.value)} 秒`;
  return formatNumber(metric.value);
}


export function buildImprovementThemes(report: FaeAnalysisReport): ImprovementTheme[] {
  const recommendations = new Map(report.recommendations.map((item) => [item.recommendation_id, item]));
  return report.findings.map((finding) => ({
    finding,
    recommendations: finding.recommendation_ids
      .map((id) => recommendations.get(id))
      .filter((item): item is FaeReportRecommendation => Boolean(item?.finding_ids.includes(finding.finding_id)))
      .sort((left, right) => (PRIORITY_ORDER[left.priority] ?? 99) - (PRIORITY_ORDER[right.priority] ?? 99)
        || left.recommendation_id.localeCompare(right.recommendation_id)),
  })).sort((left, right) => {
    const leftPriority = Math.min(...left.recommendations.map((item) => PRIORITY_ORDER[item.priority] ?? 99), 99);
    const rightPriority = Math.min(...right.recommendations.map((item) => PRIORITY_ORDER[item.priority] ?? 99), 99);
    return leftPriority - rightPriority || left.finding.finding_id.localeCompare(right.finding.finding_id);
  });
}
