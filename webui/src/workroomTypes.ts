export type WorkroomSourceKind =
  | "brain_thinking"
  | "brain_message"
  | "agent_thinking"
  | "agent_work"
  | "agent_message"
  | "platform_fact";

export type WorkroomTaskStatus =
  | "queued"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "timed_out"
  | "unavailable"
  | "cancelled";

export type WorkroomStatus =
  | "running"
  | "completed"
  | "partially_completed"
  | "failed"
  | "cancelled";

export interface WorkroomTimelineItem {
  eventId: string;
  taskId: string | null;
  seq: number;
  sourceKind: WorkroomSourceKind;
  sourceLabel: string;
  text: string;
  createdAt: string;
  interrupted: boolean;
}

export interface WorkroomTask {
  taskId: string;
  childSessionId: string;
  agentId: string;
  agentLabel: string;
  objective: string;
  publicReason: string;
  status: WorkroomTaskStatus;
  lastUpdate: string | null;
  artifactCount: number;
}

export interface WorkroomDeliverable {
  eventId: string;
  taskId: string;
  attachmentRef: string;
  label: string;
}

export interface WorkroomAction {
  actionId: string;
  taskId: string;
  actionKind: string;
  status: "pending" | "confirmed" | "rejected" | "expired" | "superseded";
  executionStatus: "not_started" | "queued" | "running" | "completed" | "failed";
  summary: string;
  impact: string;
  actionDigest: string;
  expiresAt: string;
  confirmedAt: string | null;
  confirmedBy: string | null;
}

export interface WorkroomTurn {
  turnId: string;
  status: WorkroomStatus;
  defaultExpanded: boolean;
  actions: WorkroomAction[];
  tasks: WorkroomTask[];
  timeline: WorkroomTimelineItem[];
  deliverables: WorkroomDeliverable[];
}
