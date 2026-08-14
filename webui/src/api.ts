import type {
  AgentRuntimeView, AgentSummary, ClusterSnapshot, FleetOverview, FlywheelOverview,
  ImprovementItem, Page, SessionDetail, SessionSummary, SyncStatus, TraceDetail,
  EventSeverity, OperationalEvent, OperationsBrief,
  FeedbackIssueDetail, FeedbackIssueSummary, ReviewInboxItem, ReviewOverview,
  ReplayRun, TurnClosureSummary, DeploymentInfo,
} from "./types";
import { platformPath } from "./auth";


export class ReviewApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(`review API ${status}`);
  }
}


async function readJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(platformPath(path), init);
  if (!response.ok) {
    let detail: unknown = null;
    try { detail = await response.json(); } catch { detail = null; }
    throw new ReviewApiError(response.status, detail);
  }
  return response.json();
}


async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  return readJson<T>(path, { signal });
}


async function writeReview<T>(
  path: string,
  actor: string,
  init: RequestInit,
): Promise<T> {
  const identity = actor.trim();
  if (!identity || identity === "web-reviewer" || identity === "anonymous") {
    throw new Error("需要可追责的复审身份");
  }
  return readJson<T>(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Review-Actor": identity,
      ...init.headers,
    },
  });
}


export async function fetchClusterStatus(
  signal?: AbortSignal,
): Promise<ClusterSnapshot> {
  const response = await fetch(platformPath("/api/cluster/status"), { signal });
  if (!response.ok) throw new Error(`cluster ${response.status}`);
  return response.json();
}


export async function fetchFleetOverview(
  signal?: AbortSignal,
): Promise<FleetOverview> {
  const response = await fetch(platformPath("/api/fleet/overview"), { signal });
  if (!response.ok) throw new Error(`fleet ${response.status}`);
  return response.json();
}

export const fetchDeployment = (signal?: AbortSignal) =>
  read<DeploymentInfo>("/api/deployment", signal);

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
export interface AttachmentTicket {
  ticket: string;
  expires_at: string;
  content_path: string;
}

export const createAttachmentTicket = (
  attachmentId: string,
  purpose: "preview" | "download",
) => readJson<AttachmentTicket>(`/api/attachments/${encodeURIComponent(attachmentId)}/ticket`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ purpose }),
});
export const fetchTrace = (turnKey: string, signal?: AbortSignal) =>
  read<TraceDetail>(`/api/turns/${encodeURIComponent(turnKey)}/trace`, signal);
export const fetchFlywheelOverview = (signal?: AbortSignal) =>
  read<FlywheelOverview>("/api/flywheel/overview", signal);
export const fetchFlywheelItems = (signal?: AbortSignal) =>
  read<Page<ImprovementItem>>("/api/flywheel/items?limit=100", signal);
export const fetchSyncStatus = (signal?: AbortSignal) =>
  read<SyncStatus[]>("/api/sync/status", signal);

function reviewQuery(agentId: string, extras: Record<string, string> = {}) {
  const params = new URLSearchParams({ agent_id: agentId, ...extras });
  return params.toString();
}

export const fetchReviewOverview = (agentId: string, signal?: AbortSignal) =>
  read<ReviewOverview>(`/api/review/overview?${reviewQuery(agentId)}`, signal);
export const fetchReviewInbox = (agentId: string, signal?: AbortSignal) =>
  read<ReviewInboxItem[]>(`/api/review/inbox?${reviewQuery(agentId, { limit: "200" })}`, signal);
export const fetchReviewIssues = (agentId: string, signal?: AbortSignal) =>
  read<FeedbackIssueSummary[]>(`/api/review/issues?${reviewQuery(agentId, { limit: "200" })}`, signal);
export const fetchReviewTurnSummaries = (turnKeys: string[], signal?: AbortSignal) => {
  const params = new URLSearchParams();
  turnKeys.forEach((turnKey) => params.append("turn_key", turnKey));
  return read<TurnClosureSummary[]>(`/api/review/turn-summaries?${params}`, signal);
};
export const fetchReviewIssue = (id: string, signal?: AbortSignal) =>
  read<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(id)}`, signal);

export const createReviewIssue = (
  payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>("/api/review/issues", actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const updateReviewIssue = (
  id: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(id)}`, actor, {
  method: "PATCH", body: JSON.stringify(payload),
});
export const linkReviewTurn = (
  id: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(id)}/links`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const moveReviewLink = (
  issueId: string, linkId: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(issueId)}/links/${encodeURIComponent(linkId)}/move`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const markFixReady = (
  id: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(id)}/fix-ready`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const mergeReviewIssue = (
  id: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(id)}/merge`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const addFixEvidence = (
  id: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(id)}/evidence`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const verifyFixEvidence = (
  evidenceId: string, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/evidence/${encodeURIComponent(evidenceId)}/verify`, actor, {
  method: "POST", body: JSON.stringify({ reason: "machine verification requested" }),
});
export const startReplay = (
  issueId: string, payload: Record<string, unknown>, actor: string,
) => writeReview<ReplayRun>(`/api/review/issues/${encodeURIComponent(issueId)}/replays`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const reviewReplay = (
  replayId: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/replays/${encodeURIComponent(replayId)}/semantic-review`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
export const setIssueDisposition = (
  issueId: string, payload: Record<string, unknown>, actor: string,
) => writeReview<FeedbackIssueDetail>(`/api/review/issues/${encodeURIComponent(issueId)}/disposition`, actor, {
  method: "POST", body: JSON.stringify(payload),
});
