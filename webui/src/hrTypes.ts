export type PositionSource = "official_site" | "manual";
export type OfficialPositionStatus = "active" | "stale" | "suspected_inactive" | "inactive";
export type InternalPositionStatus = "draft" | "active" | "archived";
export type PositionDraftState = "proposed" | "confirmed" | "merged" | "dismissed";

export interface HrPosition {
  positionId: string;
  sourceKind: PositionSource;
  officialJobId: string | null;
  title: string;
  department: string | null;
  locations: string[];
  officialStatus: OfficialPositionStatus | null;
  internalStatus: InternalPositionStatus;
  sourceVersion: string | null;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface HrPositionDetail extends HrPosition {
  conversationCount: number;
  materialCount: number;
  artifactCount: number;
  conversationIds: string[];
  materialAttachmentIds: string[];
  artifactIds: string[];
  artifactAttachmentIds: string[];
}

export interface HrPositionDraft {
  draftId: string;
  sourceKind: "historical_conversation" | "new_conversation";
  sourceKey: string;
  sourceConversationId: string | null;
  title: string;
  proposal: Record<string, unknown>;
  evidence: Record<string, unknown>;
  discoveryRuleVersion: string;
  state: PositionDraftState;
  resolvedPositionId: string | null;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface HrPositionPackageModules {
  mission: { text: string };
  jd: { text: string };
  jr: { text: string };
}

export interface HrPositionPackage {
  draftId: string;
  draftVersionId: string;
  conversationId: string;
  versionNumber: number;
  title: string;
  modules: HrPositionPackageModules;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface HrConfirmedPositionPackage {
  positionId: string;
  contextVersionId: string;
  conversationId: string;
}

export interface PositionPage {
  items: HrPosition[];
  nextCursor: string | null;
}

export interface ProposePositionDraftInput {
  sourceKind: "historical_conversation" | "new_conversation";
  sourceKey: string;
  sourceConversationId: string | null;
  title: string;
  proposal: Record<string, unknown>;
  evidence: Record<string, unknown>;
  discoveryRuleVersion: string;
}

export interface PositionMaterial {
  positionId: string;
  attachmentId: string;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface PositionConversationBinding {
  positionId: string;
  conversationId: string;
  bindingKind: "created_in_position" | "draft_confirmed" | "draft_merged" | "historical_exact" | "manual_correction";
  previousPositionId: string | null;
  createdAt: string;
}
