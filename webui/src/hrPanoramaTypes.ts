export type HrPanoramaRunState = "queued" | "running" | "completed" | "partially_completed" | "failed";
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

export interface HrPanoramaRun {
  runId: string;
  selectedSourceIds: string[];
  conversationId: string;
  state: HrPanoramaRunState;
  errorCode: string | null;
  sourceFailures: Record<string, string>;
  rowVersion: number;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
  updatedAt: string;
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

export interface HrPanoramaInsight {
  insightVersionId: string;
  runId: string;
  versionNumber: number;
  selectedSourceIds: string[];
  snapshotIds: string[];
  facts: HrPanoramaFact[];
  inferences: HrPanoramaInference[];
  unknowns: HrPanoramaUnknown[];
  directionClusters: Record<string, unknown>;
  summary: string;
  sourceConversationId: string;
  sourceTurnId: string;
  agentId: string;
  modelVersion: string;
  createdAt: string;
}

export interface HrPanoramaSnapshot {
  snapshotId: string;
  runId: string;
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

export interface HrPanoramaReport {
  insight: HrPanoramaInsight;
  sources: HrPanoramaSource[];
  snapshots: HrPanoramaSnapshot[];
}

export interface AddHrPanoramaCompanyInput {
  canonicalName: string;
  aliases: string[];
  approvedUrls: string[];
}

export interface StartHrPanoramaRunInput {
  sourceIds: string[];
  conversationId?: string;
}
