export type HrPanoramaJobStatus = "open" | "closed" | "unknown";

export interface HrPanoramaSource {
  sourceId: string;
  sourceKind: "company";
  canonicalName: string;
  aliases: string[];
  approvedUrls: string[];
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface HrPanoramaSourceCoverage {
  sourceId: string;
  state: "succeeded" | "failed";
  observedAt: string;
  sourceUrls: string[];
  jobCount: number;
  errorCode?: string;
  channelFailures?: Record<string, string>;
}

export interface HrPanoramaPublication {
  publicationId: string;
  batchId: string;
  insightVersionId: string;
  coverageState: "complete" | "partial";
  sourceCoverage: HrPanoramaSourceCoverage[];
  publishedAt: string;
}

export interface HrPanoramaFact {
  factId: string;
  text: string;
  snapshotId: string;
  observationId: string;
  sourceUrl: string;
  observedAt: string;
}

export interface HrPanoramaInference {
  text: string;
  basisFactIds: string[];
}

export interface HrPanoramaUnknown { text: string; }

export interface HrPanoramaV2CompanyMetrics {
  job_count: number;
  tracks: Record<string, number>;
  directions: Record<string, number>;
  job_families: Record<string, number>;
  seniority: Record<string, number>;
  locations: Record<string, number>;
  skills: Record<string, number>;
  sample_snapshot_ids: string[];
}

export interface HrPanoramaV2Dimensions {
  schema_version: 2;
  scope: {
    snapshot_count: number;
    unique_job_count: number;
    duplicate_snapshot_count: number;
    source_count: number;
    observed_from: string;
    observed_to: string;
  };
  tracks: Record<string, number>;
  directions: Record<string, number>;
  job_families: Record<string, number>;
  seniority: Record<string, number>;
  education: Record<string, number>;
  locations: Record<string, number>;
  skills: Array<{ name: string; job_count: number }>;
  company_matrix: Record<string, HrPanoramaV2CompanyMetrics>;
  evidence_samples: Record<string, Record<string, string[]>>;
  trend: { state: "baseline_only"; message: string };
  interpretation_limits: string[];
}

export interface HrPanoramaInsight {
  insightVersionId: string;
  runId: string | null;
  productionBatchId: string | null;
  versionNumber: number;
  selectedSourceIds: string[];
  snapshotIds: string[];
  facts: HrPanoramaFact[];
  inferences: HrPanoramaInference[];
  unknowns: HrPanoramaUnknown[];
  directionClusters: Record<string, unknown>;
  summary: string;
  sourceConversationId: string | null;
  sourceTurnId: string | null;
  agentId: string;
  modelVersion: string;
  createdAt: string;
}

export interface HrPanoramaSnapshot {
  snapshotId: string;
  runId: string | null;
  productionBatchId: string | null;
  observationId: string | null;
  sourceId: string;
  publicJobKey: string;
  title: string;
  location: string;
  dutyExcerpt: string;
  requirementExcerpt: string;
  sourceUrl: string;
  observedAt: string;
  contentSha256: string;
  status: HrPanoramaJobStatus;
  createdAt: string;
}

export interface HrPanoramaEvidence {
  sourceId: string;
  sourceUrl: string;
  attemptNumber: number;
  state: "succeeded" | "failed";
  errorCode: string | null;
  sha256: string;
  mime: string;
  sizeBytes: number;
  normalizedJobCount: number;
  observedAt: string;
}

export interface HrPanoramaReportSummary {
  publication: HrPanoramaPublication;
  insight: HrPanoramaInsight;
}

export interface HrPanoramaReport extends HrPanoramaReportSummary {
  sources: HrPanoramaSource[];
  snapshots: HrPanoramaSnapshot[];
  evidence: HrPanoramaEvidence[];
}
