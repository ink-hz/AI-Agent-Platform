export type HrPositionSection = "chat" | "context" | "candidates" | "artifacts";
export interface HrPositionMaterialItem {
  attachmentId: string; filename: string; mediaType: string; state: string;
  sizeBytes: number; createdAt: string; sourceConversationId: string | null;
  sourceTurnId: string | null; previewAvailable: boolean; downloadAvailable: boolean;
}
export interface HrPositionArtifactItem extends HrPositionMaterialItem {
  artifactId: string; artifactVersionId: string; artifactVersion: number;
}
export interface HrPositionResources { materials: HrPositionMaterialItem[]; artifacts: HrPositionArtifactItem[]; }
export interface HrDownloadTicket { contentPath: string; expiresAt: string; }
export interface HrOfficialPositionVersion {
  officialVersionId: string; positionId: string; officialJobId: string;
  title: string; department: string | null; locations: string[];
  category: string; subcategory: string | null; headcount: number;
  degree: string | null; employmentType: string; salary: string;
  duty: string; requirement: string; officialStatus: string; statusReason: string;
  sourceVersion: string; sourceChangedAt: string; sourceSnapshotAt: string;
  contentHash: string; firstObservedAt: string; lastObservedAt: string;
  consecutiveMisses: number; officialStatusCode: number; createdAt: string;
}
export interface HrOfficialPositionDownload { blob: Blob; filename: string; }
