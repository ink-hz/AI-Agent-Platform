/** Version 1 metadata-only contract; backend runtime_dashboard/models.py owns semantics. */
export type DashboardDays = 7 | 30;
export interface Metric {
  value: number | null;
  status: 'observed' | 'partial' | 'unavailable' | 'not_applicable';
  unit: 'session' | 'turn' | 'delivery' | 'work' | 'result' | 'document' | 'project' | 'task' | 'application' | 'agent';
  definition: string;
  reason: string | null;
}
export interface Counts { sessions: Metric; turns: Metric; answered_turns: Metric; deliveries: Metric }
export interface Usage { retained: Counts; window: Counts; last_activity_at: string | null; outcome_counts: Record<string, number> }
export interface SourceEvidence {
  source_id: string; source_kind: string; channel: string | null;
  status: 'fresh' | 'stale' | 'unavailable' | 'not_applicable';
  observed_at: string | null; published_at: string | null; checked_at: string | null;
  complete: boolean; error: string | null; channels: (string | null)[];
  permission_scope: string; reader_role: string | null;
  input_time_basis?: 'question_at' | 'record_created_at' | 'mixed' | 'inputs_created_at' | null;
  missing_question_time_records?: number | null;
}
export interface RuntimeEvidence {
  coverage: 'observed' | 'not_configured' | 'not_observed' | 'not_applicable';
  state: 'healthy' | 'degraded' | 'offline' | 'unknown';
  observed_at: string | null; checked_at: string | null; stale: boolean; method: string | null; error: string | null;
}
export interface Application {
  id: string; name: string; kind: 'agent' | 'workspace'; entry_path: string | null; can_use: boolean;
  runtime: RuntimeEvidence; source: SourceEvidence; usage: Usage;
  business_metrics: Record<string, Metric>; business_sources: Record<string, SourceEvidence>;
  deployment: { version: string | null; observed_at: string | null; evidence: string | null };
  dataset: Record<string, string> | null;
}
export interface Channel { id: string; agent_id: string; name: string; source: SourceEvidence; usage: Usage; configured: boolean; records_path: string }
export interface TimeWindow { start: string; end: string; timezone: 'Asia/Shanghai'; days: DashboardDays | null }
export interface DashboardOverview {
  schema_version: 1; generated_at: string; window: TimeWindow; rolling_24h: TimeWindow; active_window_minutes: 15;
  inventory: { applications: number; agent_services: number; business_workspaces: number; configured_agents: number; channel_agents: number; legacy_streams: number };
  summary: { healthy: number; abnormal: number; unknown: number; observed_runtime: number; expected_runtime: number; covered_sources: number; expected_sources: number; status: 'complete' | 'partial' | 'unavailable'; usage: Counts; active_agents_15m: Metric };
  applications: Application[]; channel_agents: Channel[]; legacy_streams: Channel[];
  trend: { date: string; turns: Metric; answered_turns: Metric; deliveries: Metric }[];
  last_24h: Counts; ranking: { application_id: string; turns: Metric }[];
}
