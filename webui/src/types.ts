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
