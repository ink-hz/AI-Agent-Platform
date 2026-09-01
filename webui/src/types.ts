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
export type MessageTimeStatus = "exact" | "estimated" | "unavailable";

export interface DeploymentInfo {
  mode: "local" | "cloud-replica";
  read_only: boolean;
  auth: "local" | "ssh-tunnel" | string;
  freshness: "current" | "stale" | "unavailable";
  last_success_at: string | null;
}

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
  feedback_count: number | null;
  review_count: number | null;
  feedback_availability?: Availability;
  review_availability?: Availability;
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

export interface AttachmentSummary {
  attachment_id: string;
  direction: "user_input" | "agent_output";
  display_name: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  received_or_generated_at: string;
  archive_status: "pending" | "available" | "failed" | "source_unavailable" | "expired";
  delivery_status: "pending" | "delivered" | "failed" | "not_applicable";
  expires_at: string;
  safe_category?: string | null;
  size_bucket?: string | null;
  content_available?: boolean;
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
  question_at: string | null;
  answer_at: string | null;
  question_time_status: MessageTimeStatus;
  answer_time_status: MessageTimeStatus;
  trace_key: string | null;
  outcome: string | null;
  fallback_used: boolean;
  duration_ms: number | null;
  sources: Record<string, unknown>[];
  evidence: EvidenceSummary[];
  evidence_availability: Availability;
  feedback: FeedbackItem[];
  feedback_availability?: Availability;
  feedback_summary?: Record<string, number>;
  reviews: ReviewItem[];
  review_availability?: Availability;
  review_status_summary?: Record<string, number>;
  improvements: ImprovementItem[];
  input_attachments: AttachmentSummary[];
  output_attachments: AttachmentSummary[];
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
  feedback_total: number | null;
  negative_feedback: number | null;
  pending_reviews: number | null;
  evaluation_candidates: number | null;
  knowledge_tasks: number | null;
  qa_candidates: number | null;
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

export type IssueStatus =
  | "unknown"
  | "pending_triage"
  | "fixing"
  | "awaiting_merge"
  | "awaiting_deploy"
  | "awaiting_replay"
  | "awaiting_review"
  | "closed"
  | "duplicate"
  | "not_actionable"
  | "wont_fix";

export interface IssueProgress {
  issue_id: string;
  status: IssueStatus;
  missing_gates: string[] | null;
  replay_passed_turns: number | null;
  replay_required_turns: number | null;
  reopened: boolean | null;
}

export interface ReviewOverview {
  feedback_rows: number | null;
  negative_rows: number | null;
  negative_turns: number | null;
  positive_rows: number | null;
  issue_total: number | null;
  statuses: Partial<Record<IssueStatus, number>>;
  dispositions: Record<string, number>;
  write_available: boolean;
  lifecycle_status_available?: boolean;
}

export interface TurnClosureSummary {
  turn_key: string;
  issue_id: string | null;
  status: IssueStatus;
  missing_gates: string[] | null;
  latest_valid_replay_id: string | null;
}

export interface ReviewInboxItem {
  agent_id: string;
  turn_key: string;
  question: string;
  answer: string;
  feedback_keys: string[];
  feedback_count?: number;
  first_feedback_at: string;
}

export interface FeedbackIssueSummary {
  id: string;
  agent_id: string;
  origin_turn_key: string | null;
  title: string;
  priority: "P0" | "P1" | "P2" | "P3";
  failure_layer: string | null;
  secondary_layers: string[];
  root_cause: string | null;
  impact_scope: string | null;
  owner: string | null;
  disposition: "actionable" | "duplicate" | "not_actionable" | "wont_fix";
  row_version: number | null;
  created_at?: string;
  updated_at?: string;
  progress: IssueProgress;
}

export interface IssueLink {
  id: string;
  issue_id?: string;
  active: boolean;
  link_role: "primary" | "secondary";
  agent_id: string;
  source_turn_key: string;
  source_feedback_keys: string[];
  source_question: string | null;
  source_answer: string | null;
  source_turn_index?: number | null;
  source_session_key?: string | null;
  source_created_at?: string | null;
  source_details?: Record<string, unknown> | null;
  source_sources?: Record<string, unknown>[] | null;
  source_trace_key?: string | null;
  source_outcome?: string | null;
  source_fallback_used?: boolean | null;
  source_context?: { turn_index: number; question: string; answer: string }[] | null;
}

export interface FixEvidence {
  id: string;
  evidence_type: "commit" | "pull_request" | "merge" | "deployment";
  repository: string;
  reference: string;
  url: string;
  version: string;
  commit_sha: string;
  release_manifest_ref: string;
  environment: string;
  verification_status: "pending" | "verified" | "rejected" | "revoked";
  verification_details: Record<string, unknown>;
  observed_at: string;
  observed_by: string;
}

export interface ReplayRun {
  id: string;
  issue_link_id: string;
  attempt_no: number;
  expected_version?: string;
  actual_version: string;
  expected_git_sha?: string;
  actual_git_sha: string;
  configured_model: string;
  actual_model: string;
  answer: string;
  sources: Record<string, unknown>[];
  done?: Record<string, unknown>;
  trace_id: string;
  duration_ms?: number | null;
  execution_status: "running" | "succeeded" | "failed" | "blocked";
  runtime_gate: "pending" | "passed" | "failed";
  runtime_failure_reason: string;
  semantic_verdict: "pending" | "passed" | "failed";
  review_method: "codex" | "human_fae" | null;
  reviewer: string | null;
  review_reason: string;
  started_at: string;
  completed_at: string | null;
}

export interface IssueEvent {
  id?: string;
  event_type: string;
  actor: string;
  reason: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  created_at: string;
}

export interface FeedbackIssueDetail {
  issue: Omit<FeedbackIssueSummary, "progress">;
  links: IssueLink[];
  evidence: FixEvidence[];
  replays: ReplayRun[];
  events: IssueEvent[];
  progress: IssueProgress;
  section_availability?: Partial<Record<"links" | "evidence" | "replays" | "events", Availability>>;
}

export interface ReplayMatrixRow {
  link: IssueLink;
  attempts: ReplayRun[];
  selected: ReplayRun | null;
}
