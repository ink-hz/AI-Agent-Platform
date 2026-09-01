export type FaeReportDimension = "usage" | "business_value" | "answer_effectiveness" | "insights_improvement";

export interface FaeReportMetric {
  metric_id: string;
  dimension: FaeReportDimension;
  label: string;
  value: number | Record<string, number | string>;
  unit: string;
  numerator: number | null;
  denominator: number | null;
  filters: string[];
  assumptions: string[];
  evidence_artifact_refs: string[];
}

export interface FaeReportEvidence {
  kind: "session" | "turn" | "feedback" | "issue";
  label: string;
  canonical_key?: string;
  replica_key?: string;
}

export interface FaeReportFinding {
  finding_id: string;
  dimension: FaeReportDimension;
  severity: string;
  title: string;
  description: string;
  root_cause_hypothesis: string;
  impact_scope: string;
  metric_ids: string[];
  evidence_refs: FaeReportEvidence[];
  recommendation_ids: string[];
  linked_issue_ids: string[];
}

export interface FaeReportRecommendation {
  recommendation_id: string;
  dimension: FaeReportDimension;
  priority: string;
  title: string;
  rationale: string;
  proposed_action: string;
  owner_role: string;
  finding_ids: string[];
  success_metric_ids: string[];
}

export interface FaeAnalysisReport {
  schema_name: "fae.analysis-report";
  schema_version: "1.0.0";
  report_id: string;
  report_version: number;
  report_type: "weekly" | "topic";
  status: "ready" | "failed";
  title: string;
  period: { start_at: string; end_at: string };
  data_cutoff_at: string;
  generated_at: string;
  analysis_version: string;
  source: { agent_id: "ai-fae-agent"; source_kind: "fae"; environment: "production"; source_snapshot_at: string; session_count: number; turn_count: number; feedback_event_count: number; reviewed_session_count: number };
  summary: { headline: string; overview: string; top_finding_ids: string[]; top_recommendation_ids: string[] } | null;
  metrics: FaeReportMetric[];
  findings: FaeReportFinding[];
  recommendations: FaeReportRecommendation[];
  cases: Array<{ case_id: string; dimension: FaeReportDimension; title: string; scenario: string; outcome: string; evidence_refs: FaeReportEvidence[]; business_case_approved: true }>;
  failure: { stage: string; code: string; message: string; retryable: boolean } | null;
  publication: { payload_digest: string; imported_at: string } | null;
  latest_source_sync_at: string | null;
  currentness: "current" | "source_updated";
}

const DIMENSIONS = new Set<FaeReportDimension>(["usage", "business_value", "answer_effectiveness", "insights_improvement"]);

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("FAE report response contract invalid");
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string" || !value) throw new Error("FAE report response contract invalid");
  return value;
}

export function parseFaeReport(value: unknown): FaeAnalysisReport {
  const raw = object(value);
  if (raw.schema_name !== "fae.analysis-report" || raw.schema_version !== "1.0.0"
    || (raw.status !== "ready" && raw.status !== "failed") || !Array.isArray(raw.metrics)
    || !Array.isArray(raw.findings) || !Array.isArray(raw.recommendations) || !Array.isArray(raw.cases)) {
    throw new Error("FAE report response contract invalid");
  }
  const dimensions = new Set(raw.metrics.map((item) => object(item).dimension));
  if (raw.status === "ready" && [...DIMENSIONS].some((dimension) => !dimensions.has(dimension))) {
    throw new Error("FAE report response contract invalid");
  }
  text(raw.report_id); text(raw.title); object(raw.period); object(raw.source);
  if (!Number.isSafeInteger(raw.report_version) || Number(raw.report_version) < 1) throw new Error("FAE report response contract invalid");
  return raw as unknown as FaeAnalysisReport;
}
