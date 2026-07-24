import type {
  AgentRuntimeView, AgentSummary, ClusterSnapshot, FleetOverview, FlywheelOverview,
  ImprovementItem, Page, SessionDetail, SessionSummary, SyncStatus, TraceDetail,
  EventSeverity, OperationalEvent, OperationsBrief,
} from "./types";


async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}


export async function fetchClusterStatus(
  signal?: AbortSignal,
): Promise<ClusterSnapshot> {
  const response = await fetch("/api/cluster/status", { signal });
  if (!response.ok) throw new Error(`cluster ${response.status}`);
  return response.json();
}


export async function fetchFleetOverview(
  signal?: AbortSignal,
): Promise<FleetOverview> {
  const response = await fetch("/api/fleet/overview", { signal });
  if (!response.ok) throw new Error(`fleet ${response.status}`);
  return response.json();
}

export const fetchOperationsBrief = (signal?: AbortSignal) =>
  read<OperationsBrief>("/api/operations/brief", signal);

export interface OperationsEventQuery {
  agent_id?: string;
  event_type?: string;
  severity?: EventSeverity;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export function fetchOperationalEvents(
  query: OperationsEventQuery = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  const suffix = params.size ? `?${params}` : "";
  return read<Page<OperationalEvent>>(`/api/operations/events${suffix}`, signal);
}

export const fetchAgents = (signal?: AbortSignal) => read<AgentSummary[]>("/api/agents", signal);
export const fetchAgent = (id: string, signal?: AbortSignal) =>
  read<AgentSummary>(`/api/agents/${encodeURIComponent(id)}`, signal);
export const fetchAgentRuntime = (id: string, signal?: AbortSignal) =>
  read<AgentRuntimeView>(`/api/agents/${encodeURIComponent(id)}/runtime`, signal);

export interface SessionQuery {
  agent_id?: string;
  source_kind?: string;
  q?: string;
  sentiment?: string;
  review_status?: string;
  limit?: number;
  offset?: number;
}

export function fetchSessions(query: SessionQuery = {}, signal?: AbortSignal) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  const suffix = params.size ? `?${params}` : "";
  return read<Page<SessionSummary>>(`/api/sessions${suffix}`, signal);
}

export const fetchSession = (key: string, signal?: AbortSignal) =>
  read<SessionDetail>(`/api/sessions/${encodeURIComponent(key)}`, signal);
export const fetchTrace = (turnKey: string, signal?: AbortSignal) =>
  read<TraceDetail>(`/api/turns/${encodeURIComponent(turnKey)}/trace`, signal);
export const fetchFlywheelOverview = (signal?: AbortSignal) =>
  read<FlywheelOverview>("/api/flywheel/overview", signal);
export const fetchFlywheelItems = (signal?: AbortSignal) =>
  read<Page<ImprovementItem>>("/api/flywheel/items?limit=100", signal);
export const fetchSyncStatus = (signal?: AbortSignal) =>
  read<SyncStatus[]>("/api/sync/status", signal);
