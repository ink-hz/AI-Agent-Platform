export type HrTaskKind = "jd" | "jr" | "talent_profile" | "sourcing_strategy"
  | "position_interview_plan" | "candidate_match" | "candidate_interview_plan"
  | "candidate_comparison";
export type HrPositionSection = "chat" | "context" | "candidates" | "artifacts";
export type HrDraftState = "pending" | "parsing" | "failed" | "confirmed";

export interface HrPositionMaterialItem { attachmentId: string; filename: string; mediaType: string; state: string; sizeBytes: number; createdAt: string; sourceConversationId: string | null; sourceTurnId: string | null; previewAvailable: boolean; downloadAvailable: boolean; }
export interface HrPositionArtifactItem extends HrPositionMaterialItem { artifactId: string; artifactVersion: number; }
export interface HrPositionResources { materials: HrPositionMaterialItem[]; artifacts: HrPositionArtifactItem[]; }
export interface HrDownloadTicket { contentPath: string; expiresAt: string; }
export interface HrContextVersion { contextVersionId: string; displayVersion: number; status: "draft" | "confirmed" | "superseded"; summary: string; modules: Record<string, string>; rowVersion: number; createdAt: string; }
export interface HrCandidateDraft { draftId: string; filename: string; state: HrDraftState; candidateName: string | null; error: string | null; attachmentId: string; }
export interface HrPositionCandidate { positionCandidateId: string; candidateId: string; name: string; contextVersionId: string; state: "active" | "archived"; }
export interface HrTaskRecord { taskId: string; status: "accepted" | "running" | "completed" | "failed"; taskKind: HrTaskKind; }
