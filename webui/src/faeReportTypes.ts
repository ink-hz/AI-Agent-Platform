export type FaeReportDimension = "usage" | "business_value" | "answer_effectiveness" | "insights_improvement";
export type FaeReportCurrentness = "current" | "source_updated";
export type FaeReportUnit = "count" | "ratio" | "percent" | "milliseconds" | "seconds" | "distribution" | "milliseconds_distribution";

export interface FaeReportMetric {
  metric_id: string;
  dimension: FaeReportDimension;
  label: string;
  value: number | Record<string, number | string>;
  unit: FaeReportUnit;
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
  severity: "critical" | "high" | "medium" | "low" | "opportunity";
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
  priority: "p0" | "p1" | "p2" | "p3";
  title: string;
  rationale: string;
  proposed_action: string;
  owner_role: string;
  finding_ids: string[];
  success_metric_ids: string[];
}

export interface FaeReportFailure {
  stage: string;
  code: string;
  message: string;
  retryable: boolean;
}

export interface FaeReportPublication {
  payload_digest: string;
  imported_at: string;
}

export interface FaeReportSummary {
  report_id: string;
  report_version: number;
  report_type: "weekly" | "topic";
  status: "ready" | "failed";
  title: string;
  period: { start_at: string; end_at: string };
  data_cutoff_at: string;
  generated_at: string;
  analysis_version: string;
  failure: FaeReportFailure | null;
  publication: FaeReportPublication | null;
  latest_source_sync_at: string | null;
  currentness: FaeReportCurrentness;
}

export interface FaeAnalysisReport extends FaeReportSummary {
  schema_name: "fae.analysis-report";
  schema_version: "1.0.0";
  source: { agent_id: "ai-fae-agent"; source_kind: "fae"; environment: "production"; source_snapshot_at: string; session_count: number; turn_count: number; feedback_event_count: number; reviewed_session_count: number };
  summary: { headline: string; overview: string; top_finding_ids: string[]; top_recommendation_ids: string[] } | null;
  metrics: FaeReportMetric[];
  findings: FaeReportFinding[];
  recommendations: FaeReportRecommendation[];
  cases: Array<{ case_id: string; dimension: FaeReportDimension; title: string; scenario: string; outcome: string; evidence_refs: FaeReportEvidence[]; business_case_approved: true }>;
  artifact_digests: Record<string, string>;
}

const ERROR = "FAE report response contract invalid";
const DIMENSIONS = new Set<FaeReportDimension>(["usage", "business_value", "answer_effectiveness", "insights_improvement"]);
const UNITS = new Set<FaeReportUnit>(["count", "ratio", "percent", "milliseconds", "seconds", "distribution", "milliseconds_distribution"]);
const TOP_LEVEL_KEYS = new Set([
  "schema_name", "schema_version", "report_id", "report_version", "report_type", "status", "title",
  "period", "data_cutoff_at", "generated_at", "analysis_version", "source", "summary", "metrics",
  "findings", "recommendations", "cases", "artifact_digests", "failure", "publication",
  "latest_source_sync_at", "currentness",
]);
const SUMMARY_KEYS = new Set([
  "report_id", "report_version", "report_type", "status", "title", "period", "data_cutoff_at",
  "generated_at", "analysis_version", "failure", "publication", "latest_source_sync_at", "currentness",
]);

function invalid(): never { throw new Error(ERROR); }

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}

function exactObject(value: unknown, keys: ReadonlySet<string>): Record<string, unknown> {
  const raw = object(value);
  if (Object.keys(raw).some((key) => !keys.has(key)) || [...keys].some((key) => !(key in raw))) invalid();
  return raw;
}

function text(value: unknown): string {
  if (typeof value !== "string" || !value) invalid();
  return value;
}

function timestamp(value: unknown): string {
  const selected = text(value);
  if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(selected) || !Number.isFinite(Date.parse(selected))) invalid();
  return selected;
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) invalid();
  return Number(value);
}

function nonNegativeInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) invalid();
  return Number(value);
}

function nullableNumber(value: unknown, positive = false): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || (positive && value === 0)) invalid();
  return value;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item)) invalid();
  return [...value];
}

function parsePeriod(value: unknown): { start_at: string; end_at: string } {
  const raw = exactObject(value, new Set(["start_at", "end_at"]));
  return { start_at: timestamp(raw.start_at), end_at: timestamp(raw.end_at) };
}

function parseFailure(value: unknown): FaeReportFailure | null {
  if (value === null) return null;
  const raw = exactObject(value, new Set(["stage", "code", "message", "retryable"]));
  if (typeof raw.retryable !== "boolean") invalid();
  return { stage: text(raw.stage), code: text(raw.code), message: text(raw.message), retryable: raw.retryable };
}

function parsePublication(value: unknown): FaeReportPublication | null {
  if (value === null) return null;
  const raw = exactObject(value, new Set(["payload_digest", "imported_at"]));
  const digest = text(raw.payload_digest);
  if (!/^[0-9a-f]{64}$/.test(digest)) invalid();
  return { payload_digest: digest, imported_at: timestamp(raw.imported_at) };
}

function parseCommonSummary(value: unknown): FaeReportSummary {
  const raw = exactObject(value, SUMMARY_KEYS);
  if ((raw.report_type !== "weekly" && raw.report_type !== "topic")
    || (raw.status !== "ready" && raw.status !== "failed")
    || (raw.currentness !== "current" && raw.currentness !== "source_updated")) invalid();
  return {
    report_id: text(raw.report_id),
    report_version: positiveInteger(raw.report_version),
    report_type: raw.report_type,
    status: raw.status,
    title: text(raw.title),
    period: parsePeriod(raw.period),
    data_cutoff_at: timestamp(raw.data_cutoff_at),
    generated_at: timestamp(raw.generated_at),
    analysis_version: text(raw.analysis_version),
    failure: parseFailure(raw.failure),
    publication: parsePublication(raw.publication),
    latest_source_sync_at: raw.latest_source_sync_at === null ? null : timestamp(raw.latest_source_sync_at),
    currentness: raw.currentness,
  };
}

function parseMetric(value: unknown): FaeReportMetric {
  const raw = exactObject(value, new Set([
    "metric_id", "dimension", "label", "value", "unit", "numerator", "denominator",
    "filters", "assumptions", "evidence_artifact_refs",
  ]));
  if (!DIMENSIONS.has(raw.dimension as FaeReportDimension) || !UNITS.has(raw.unit as FaeReportUnit)) invalid();
  const unit = raw.unit as FaeReportUnit;
  let metricValue: number | Record<string, number | string>;
  if (unit === "distribution" || unit === "milliseconds_distribution") {
    const distribution = object(raw.value);
    if (!Object.keys(distribution).length || Object.values(distribution).some((item) => (
      typeof item !== "number" && item !== "少于 5"
    ))) invalid();
    metricValue = distribution as Record<string, number | string>;
  } else {
    if (typeof raw.value !== "number" || !Number.isFinite(raw.value)) invalid();
    metricValue = raw.value;
  }
  const numerator = nullableNumber(raw.numerator);
  const denominator = nullableNumber(raw.denominator, true);
  if ((unit === "ratio" || unit === "percent") && (numerator === null || denominator === null)) invalid();
  return {
    metric_id: text(raw.metric_id), dimension: raw.dimension as FaeReportDimension, label: text(raw.label),
    value: metricValue, unit, numerator, denominator, filters: stringArray(raw.filters),
    assumptions: stringArray(raw.assumptions), evidence_artifact_refs: stringArray(raw.evidence_artifact_refs),
  };
}

function parseEvidence(value: unknown): FaeReportEvidence {
  const allowed = new Set(["kind", "label", "canonical_key", "replica_key"]);
  const raw = object(value);
  if (Object.keys(raw).some((key) => !allowed.has(key)) || !("kind" in raw) || !("label" in raw)) invalid();
  if (!["session", "turn", "feedback", "issue"].includes(String(raw.kind))) invalid();
  if (raw.canonical_key === undefined && raw.replica_key === undefined) invalid();
  if (raw.canonical_key !== undefined) text(raw.canonical_key);
  if (raw.replica_key !== undefined) text(raw.replica_key);
  text(raw.label);
  return raw as unknown as FaeReportEvidence;
}

function parseFinding(value: unknown): FaeReportFinding {
  const raw = exactObject(value, new Set([
    "finding_id", "dimension", "severity", "title", "description", "root_cause_hypothesis",
    "impact_scope", "metric_ids", "evidence_refs", "recommendation_ids", "linked_issue_ids",
  ]));
  if (!DIMENSIONS.has(raw.dimension as FaeReportDimension)
    || !["critical", "high", "medium", "low", "opportunity"].includes(String(raw.severity))
    || !Array.isArray(raw.evidence_refs)) invalid();
  return {
    finding_id: text(raw.finding_id), dimension: raw.dimension as FaeReportDimension,
    severity: raw.severity as FaeReportFinding["severity"], title: text(raw.title),
    description: text(raw.description), root_cause_hypothesis: text(raw.root_cause_hypothesis),
    impact_scope: text(raw.impact_scope), metric_ids: stringArray(raw.metric_ids),
    evidence_refs: raw.evidence_refs.map(parseEvidence), recommendation_ids: stringArray(raw.recommendation_ids),
    linked_issue_ids: stringArray(raw.linked_issue_ids),
  };
}

function parseRecommendation(value: unknown): FaeReportRecommendation {
  const raw = exactObject(value, new Set([
    "recommendation_id", "dimension", "priority", "title", "rationale", "proposed_action",
    "owner_role", "finding_ids", "success_metric_ids",
  ]));
  if (!DIMENSIONS.has(raw.dimension as FaeReportDimension) || !["p0", "p1", "p2", "p3"].includes(String(raw.priority))) invalid();
  return {
    recommendation_id: text(raw.recommendation_id), dimension: raw.dimension as FaeReportDimension,
    priority: raw.priority as FaeReportRecommendation["priority"], title: text(raw.title),
    rationale: text(raw.rationale), proposed_action: text(raw.proposed_action), owner_role: text(raw.owner_role),
    finding_ids: stringArray(raw.finding_ids), success_metric_ids: stringArray(raw.success_metric_ids),
  };
}

function parseCase(value: unknown): FaeAnalysisReport["cases"][number] {
  const raw = exactObject(value, new Set([
    "case_id", "dimension", "title", "scenario", "outcome", "evidence_refs", "business_case_approved",
  ]));
  if (!DIMENSIONS.has(raw.dimension as FaeReportDimension) || raw.business_case_approved !== true || !Array.isArray(raw.evidence_refs)) invalid();
  return {
    case_id: text(raw.case_id), dimension: raw.dimension as FaeReportDimension, title: text(raw.title),
    scenario: text(raw.scenario), outcome: text(raw.outcome), evidence_refs: raw.evidence_refs.map(parseEvidence),
    business_case_approved: true,
  };
}

export function parseFaeReportSummaryList(value: unknown): FaeReportSummary[] {
  if (!Array.isArray(value)) invalid();
  return value.map(parseCommonSummary);
}

export function parseFaeReport(value: unknown): FaeAnalysisReport {
  const raw = exactObject(value, TOP_LEVEL_KEYS);
  if (raw.schema_name !== "fae.analysis-report" || raw.schema_version !== "1.0.0") invalid();
  const common = parseCommonSummary(Object.fromEntries([...SUMMARY_KEYS].map((key) => [key, raw[key]])));
  if (!Array.isArray(raw.metrics) || !Array.isArray(raw.findings) || !Array.isArray(raw.recommendations)
    || !Array.isArray(raw.cases)) invalid();
  const metrics = raw.metrics.map(parseMetric);
  const findings = raw.findings.map(parseFinding);
  const recommendations = raw.recommendations.map(parseRecommendation);
  const dimensions = new Set(metrics.map((item) => item.dimension));
  if (common.status === "ready" && [...DIMENSIONS].some((dimension) => !dimensions.has(dimension))) invalid();
  const source = exactObject(raw.source, new Set([
    "agent_id", "source_kind", "environment", "source_snapshot_at", "session_count", "turn_count",
    "feedback_event_count", "reviewed_session_count",
  ]));
  if (source.agent_id !== "ai-fae-agent" || source.source_kind !== "fae" || source.environment !== "production") invalid();
  const reportSummary = raw.summary === null ? null : exactObject(raw.summary, new Set([
    "headline", "overview", "top_finding_ids", "top_recommendation_ids",
  ]));
  const digests = object(raw.artifact_digests);
  if (Object.values(digests).some((item) => typeof item !== "string" || !/^[0-9a-f]{64}$/.test(item))) invalid();
  if (common.status === "ready" && reportSummary === null) invalid();
  if (common.status === "failed" && (reportSummary !== null || metrics.length || findings.length || recommendations.length || raw.cases.length)) invalid();
  return {
    ...common,
    schema_name: "fae.analysis-report",
    schema_version: "1.0.0",
    source: {
      agent_id: "ai-fae-agent", source_kind: "fae", environment: "production",
      source_snapshot_at: timestamp(source.source_snapshot_at), session_count: nonNegativeInteger(source.session_count),
      turn_count: nonNegativeInteger(source.turn_count), feedback_event_count: nonNegativeInteger(source.feedback_event_count),
      reviewed_session_count: nonNegativeInteger(source.reviewed_session_count),
    },
    summary: reportSummary === null ? null : {
      headline: text(reportSummary.headline), overview: text(reportSummary.overview),
      top_finding_ids: stringArray(reportSummary.top_finding_ids),
      top_recommendation_ids: stringArray(reportSummary.top_recommendation_ids),
    },
    metrics,
    findings,
    recommendations,
    cases: raw.cases.map(parseCase),
    artifact_digests: digests as Record<string, string>,
  };
}
