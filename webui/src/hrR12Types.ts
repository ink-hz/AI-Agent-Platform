export type HrTaskKind = "jd" | "jr" | "talent_profile" | "sourcing_strategy"
  | "position_interview_plan" | "candidate_match" | "candidate_interview_plan"
  | "candidate_comparison";
export type HrCandidateTaskKind = "candidate_match" | "candidate_interview_plan";
export type HrPositionTaskKind = "jd" | "jr" | "talent_profile" | "sourcing_strategy" | "position_interview_plan";
export type HrStartableTaskKind = HrCandidateTaskKind | HrPositionTaskKind;
export type HrPositionSection = "chat" | "context" | "candidates" | "artifacts";
export type HrDraftState = "pending" | "processing" | "ready" | "failed" | "confirmed" | "dismissed";

export interface HrPositionMaterialItem {
  attachmentId: string; filename: string; mediaType: string; state: string;
  sizeBytes: number; createdAt: string; sourceConversationId: string | null;
  sourceTurnId: string | null; previewAvailable: boolean; downloadAvailable: boolean;
}
export interface HrPositionArtifactItem extends HrPositionMaterialItem { artifactId: string; artifactVersion: number; }
export interface HrPositionResources { materials: HrPositionMaterialItem[]; artifacts: HrPositionArtifactItem[]; }
export interface HrDownloadTicket { contentPath: string; expiresAt: string; }

export interface HrContextVersion {
  contextVersionId: string; positionId: string; displayVersion: number;
  status: "draft" | "confirmed" | "superseded"; summary: string;
  modules: Record<string, Record<string, unknown>>; officialVersionId: string | null;
  baseContextVersionId: string | null; sourceConversationId: string | null;
  sourceTurnId: string | null; sourceArtifactVersionId: string | null;
  sourceMaterialAttachmentIds: string[]; agentId: string | null; modelVersion: string | null;
  rowVersion: number; createdAt: string; confirmedAt: string | null;
}
export interface HrContextComparison {
  leftVersionId: string; rightVersionId: string; changedModules: string[];
  left: Record<string, unknown>; right: Record<string, unknown>;
}

export interface HrCandidateDraft {
  draftId: string; positionId: string; attachmentId: string; batchRequestId: string;
  state: HrDraftState; extractedFacts: Record<string, unknown>; identityCandidateIds: string[];
  errorCode: string | null; rowVersion: number; createdAt: string; updatedAt: string;
}
export interface HrCandidate { candidateId: string; stableName: string; facts: Record<string, unknown>; createdAt: string; updatedAt: string; }
export interface HrCandidateDocument {
  documentId: string; candidateId: string; attachmentId: string; sourceDraftId: string;
  documentKind: string; versionNumber: number; contentSha256: string;
  status: "active" | "erased"; createdAt: string;
}
export interface HrPositionCandidate {
  positionCandidateId: string; positionId: string; candidateId: string; contextVersionId: string;
  sourceDraftId: string; status: "active" | "archived"; rowVersion: number;
  createdAt: string; updatedAt: string;
}
export interface HrConfirmedCandidate { candidate: HrCandidate; document: HrCandidateDocument; positionCandidate: HrPositionCandidate; }
export type HrCandidateAnalysisKind = "resume_extract" | "match" | "candidate_interview_plan" | "comparison";
export interface HrCandidateAnalysisVersion {
  analysisVersionId: string; positionCandidateId: string; positionId: string;
  candidateId: string; contextVersionId: string; versionNumber: number;
  analysisKind: HrCandidateAnalysisKind; documentIds: string[]; feedbackIds: string[];
  result: Record<string, unknown>; evidence: Record<string, unknown>[]; unknowns: string[];
  conflicts: string[]; verificationQuestions: string[]; agentVersion: string;
  modelVersion: string; createdAt: string;
}
export interface HrHumanFeedback {
  feedbackId: string; positionCandidateId: string; analysisVersionId: string;
  feedbackKind: "accepted" | "rejected" | "correction"; conclusionKey: string;
  correction: string | null; reason: string; createdAt: string;
}
export interface HrTaskRecord {
  taskId: string;
  status: "accepted" | "running" | "completed" | "failed";
  taskKind: HrTaskKind;
  error: string | null;
  positionCandidateId?: string | null;
  candidateId?: string | null;
}
