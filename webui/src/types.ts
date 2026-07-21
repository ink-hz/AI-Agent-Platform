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

export type FleetState = "active" | "online" | "degraded" | "offline" | "checking";

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
  state: FleetState;
  uptime_seconds: number | null;
  total_conversations: number | null;
  conversations_last_7d: number | null;
  last_activity_at: string | null;
  recent_summary: string | null;
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
