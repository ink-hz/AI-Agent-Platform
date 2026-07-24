export type InstanceState = "healthy" | "degraded" | "offline" | "checking";

export interface ClusterSummary {
  total: number;
  healthy: number;
  degraded: number;
  offline: number;
  checking: number;
}

export interface SourceStatus {
  healthy: boolean;
  checked_at: string | null;
  error: string | null;
}

export interface InstanceStatus {
  id: string;
  name: string;
  pm2_name: string;
  port: number;
  status: InstanceState;
  uptime_seconds: number | null;
  latency_ms: number | null;
  checked_at: string | null;
  error: string | null;
}

export interface ClusterSnapshot {
  summary: ClusterSummary;
  source: SourceStatus;
  instances: InstanceStatus[];
}

export type FleetState = "active" | "online" | "degraded" | "offline" | "checking" | "unknown";
export type AgentVisibility = "business" | "system";
export type LifecycleBasis = "release_artifact" | "repository_history" | "earliest_session" | "not_recorded";

export interface FleetSummary {
  total_agents: number;
  running_agents: number;
  active_agents: number;
  degraded_agents: number;
  offline_agents: number;
  checking_agents: number;
  total_conversations: number | null;
  conversations_last_7d: number | null;
  conversations_previous_7d: number | null;
  change_percent: number | null;
}

export interface TrendPoint {
  date: string;
  conversations: number;
}

export interface FleetAgent {
  id: string;
  name: string;
  domain: string;
  description: string;
  glyph: string;
  accent: string;
  visibility: AgentVisibility;
  state: FleetState;
  live_since: string | null;
  live_since_basis: LifecycleBasis;
  last_updated_at: string | null;
  last_updated_basis: LifecycleBasis;
  current_runtime_seconds: number | null;
  total_conversations: number | null;
  conversations_last_7d: number | null;
  last_activity_at: string | null;
  recent_summary: string | null;
  session_count?: number | null;
  last_synced_at?: string | null;
  data_freshness?: Freshness;
}

export interface DataSourceStatus {
  healthy: boolean;
  checked_at: string | null;
  stale: boolean;
  error: string | null;
}

export interface FleetOverview {
  summary: FleetSummary;
  trend: TrendPoint[];
  agents: FleetAgent[];
  runtime_source: DataSourceStatus;
  usage_source: DataSourceStatus;
}

export type EventSeverity = "info" | "attention" | "critical";
export type EventStatus = "active" | "resolved" | "historical";
export type EventFamily = "runtime" | "data" | "execution" | "usage" | "lifecycle" | "recovery";

export interface OperationalEvent {
  event_id: string;
  agent_id: string | null;
  agent_visibility: AgentVisibility;
  event_type: string;
  event_family: EventFamily;
  severity: EventSeverity;
  status: EventStatus;
  title: string;
  summary: string;
  source_kind: string;
  occurred_at: string;
  first_observed_at: string;
  last_observed_at: string;
  resolved_at: string | null;
  facts: Record<string, unknown>;
  target_kind: string | null;
  target_id: string | null;
  target_path: string | null;
  fingerprint: string;
}

export interface OperationsBrief {
  period_start: string;
  period_end: string;
  freshness: { status: "current" | "partial" | "stale" | "unavailable"; evaluated_at: string | null; failed_groups: string[] };
  can_claim_healthy: boolean;
  attention: OperationalEvent[];
  usage: { conversations: number; active_agents: number; leaders: { agent_id: string; agent_name: string; conversations: number }[] };
  changes: OperationalEvent[];
}

export type SourceKind = "metabot" | "fae" | "admin";
export type Freshness = "live" | "fresh" | "stale";
export type Availability = "available" | "missing" | "unavailable" | "restricted";
export type SenderIdentityStatus = "resolved" | "name_only" | "unavailable";

export interface AgentSummary {
  id: string;
  name: string;
  domain: string;
  description: string;
  glyph: string;
  accent: string;
  visibility: AgentVisibility;
  source_kind: SourceKind;
  deployment: string;
  session_count: number;
  total_turns: number;
  last_activity_at: string | null;
  last_synced_at: string | null;
  freshness: Freshness;
}

export type ReadinessStatus = "Ready" | "Busy" | "Limited" | "Offline" | "Unknown";
export type RuntimeFreshness = "live" | "stale" | "unavailable";
export type RuntimeModelSource = "runtime" | "trace" | "configured" | "unavailable";
export type RuntimeChannelStatus = "connected" | "connecting" | "reconnecting" | "failed" | "unknown";

export interface RuntimeEvidence {
  kind: string;
  source: string;
  status: string;
  observed_at: string | null;
  summary: string;
}

export interface AgentRuntimeView {
  agent_id: string;
  readiness: {
    status: ReadinessStatus;
    reason: string;
    observed_at: string | null;
    freshness: RuntimeFreshness;
  };
  runtime: {
    engine: string | null;
    model: string;
    model_source: RuntimeModelSource;
    backend: string | null;
    channel: string | null;
    channel_status: RuntimeChannelStatus;
    active_turns: number | null;
    process_uptime_seconds: number | null;
  };
  lifecycle: {
    live_since: string | null;
    last_updated_at: string | null;
    production_runtime_seconds: number | null;
  };
  evidence: RuntimeEvidence[];
}

export interface SessionSummary {
  session_key: string;
  agent_id: string;
  source_kind: SourceKind;
  channel: string;
  title: string | null;
  created_at: string;
  last_active_at: string;
  turn_count: number;
  feedback_count: number;
  review_count: number;
  latest_outcome: string | null;
  source_synced_at: string | null;
  freshness: Freshness;
  participant_count: number | null;
  primary_sender_name: string | null;
  primary_sender_department: string | null;
  sender_identity_status: SenderIdentityStatus;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface EvidenceSummary {
  kind: string;
  title: string;
  reference: string | null;
  classification: string | null;
  availability: Availability;
  metadata: Record<string, unknown>;
}

export interface FeedbackItem {
  feedback_key: string;
  sentiment: "positive" | "negative" | "other";
  raw_rating: string;
  reason_code: string | null;
  comment: string;
  created_at: string;
  details: Record<string, unknown>;
}

export interface ReviewItem {
  review_key: string;
  status: string;
  native_priority: string;
  normalized_priority: string;
  failure_layer: string | null;
  notes: string;
  corrected_answer: string;
  reviewer: string;
  created_at: string;
  updated_at: string;
  details: Record<string, unknown>;
}

export interface ImprovementItem {
  item_key: string;
  turn_key: string | null;
  agent_id: string;
  source_kind: SourceKind;
  item_type: "evaluation" | "knowledge" | "qa";
  status: string;
  priority: string | null;
  title: string;
  summary: string;
  created_at: string;
  updated_at: string;
  source_synced_at: string | null;
  details: Record<string, unknown>;
}

export interface TurnDetail {
  turn_key: string;
  session_key: string;
  agent_id: string;
  source_kind: SourceKind;
  turn_index: number;
  question: string;
  answer: string;
  created_at: string;
  trace_key: string | null;
  outcome: string | null;
  fallback_used: boolean;
  duration_ms: number | null;
  sources: Record<string, unknown>[];
  evidence: EvidenceSummary[];
  evidence_availability: Availability;
  feedback: FeedbackItem[];
  reviews: ReviewItem[];
  improvements: ImprovementItem[];
  details: Record<string, unknown>;
  sender_name: string | null;
  sender_department: string | null;
  sender_identity_status: SenderIdentityStatus;
}

export interface SessionDetail extends SessionSummary {
  turns: TurnDetail[];
}

export interface TraceStep {
  step_key: string;
  trace_key: string;
  kind: "stage" | "span" | "tool_call" | "event";
  name: string;
  status: string | null;
  parent_step_key: string | null;
  seq: number | null;
  started_at: string | null;
  duration_ms: number | null;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  safe_metadata: Record<string, unknown>;
  error_summary: string | null;
}

export interface TraceDetail {
  trace_key: string;
  turn_key: string;
  agent_id: string;
  source_kind: SourceKind;
  status: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  engine: string | null;
  backend: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  error_class: string | null;
  error_message: string | null;
  detail_availability: Availability;
  source_synced_at: string | null;
  details: Record<string, unknown>;
  steps: TraceStep[];
}

export interface FlywheelOverview {
  feedback_total: number;
  negative_feedback: number;
  pending_reviews: number;
  evaluation_candidates: number;
  knowledge_tasks: number;
  qa_candidates: number;
}

export interface SyncStatus {
  source_kind: "fae" | "admin";
  status: "running" | "succeeded" | "failed";
  started_at: string;
  completed_at: string | null;
  source_counts: Record<string, number>;
  applied_counts: Record<string, number>;
  validation: Record<string, unknown>;
  error_summary: string | null;
  freshness: Freshness;
}
