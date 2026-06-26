export interface Agent {
  id: string;
  name: string;
  domain: string;
  description: string;
  icon: string;
  owner: string;
  env: string;
  status: string;
  entry_url: string;
  version: string;
  tags: string[];
}

export interface Metric {
  label: string;
  value: string | number;
}

export interface Health {
  id: string;
  online: boolean | null;
  checked_at: string | null;
  latency_ms: number | null;
  version: string | null;
  metrics: Metric[];
}
