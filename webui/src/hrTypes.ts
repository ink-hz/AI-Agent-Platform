export type PositionSource = "official_site" | "manual";
export type OfficialPositionStatus = "active" | "stale" | "suspected_inactive" | "inactive";
export type InternalPositionStatus = "draft" | "active" | "archived";

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

export interface PositionPage {
  items: HrPosition[];
  nextCursor: string | null;
}
