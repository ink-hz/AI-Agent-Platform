export type MissionMode = "brain" | "direct_agent";

export type MissionStatus =
  | "planning"
  | "delegated"
  | "synthesizing"
  | "completed"
  | "partially_completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface Mission {
  mission_id: string;
  mode: MissionMode;
  direct_agent_id: string | null;
  status: MissionStatus;
  cancel_requested: boolean;
  row_version: number;
  created_at: string;
  updated_at: string;
  terminal_at: string | null;
  prompt: string;
  content_available: boolean;
}

export interface MissionPage {
  items: Mission[];
  next_cursor: string | null;
}

export interface MissionEvent {
  event_id: string;
  mission_id: string;
  run_id: string | null;
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentCapabilityCard {
  agent_id: string;
  display_name: string;
  domain_group: string;
  mission: string;
  capabilities: string[];
  exclusions: string[];
  example_tasks: string[];
  required_inputs: string[];
  accepted_input_types: ["text"];
  output_types: ["text"];
  supports_attachments_in: false;
  supports_attachments_out: false;
  supports_evidence: boolean;
  supports_streaming: boolean;
  supports_cancellation: boolean;
  supports_idempotency: boolean;
  max_duration_seconds: number;
  data_classification: "internal";
  adapter_id: string;
  capability_version: number;
}

export const TERMINAL_MISSION_STATUSES = new Set<MissionStatus>([
  "completed", "partially_completed", "failed", "cancelled", "interrupted",
]);
