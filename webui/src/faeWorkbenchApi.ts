import { platformPath } from "./auth";
import type { ReviewApi } from "./components/review/ReviewWorkspace";
import { parseFaeOverview, type FaeOverview, type FaeSessionQuery } from "./faeWorkbenchTypes";
import type {
  FeedbackIssueDetail,
  FeedbackIssueSummary,
  Page,
  ReplayRun,
  ReviewInboxItem,
  ReviewOverview,
  SessionDetail,
  SessionSummary,
  TurnClosureSummary,
} from "./types";


export class FaeWorkbenchApiError extends Error {
  constructor(public readonly status: number) {
    super(`FAE workbench API ${status}`);
    this.name = "FaeWorkbenchApiError";
  }
}

export interface FaeWorkbenchApi {
  overview(signal?: AbortSignal): Promise<FaeOverview>;
  listSessions(query: FaeSessionQuery, signal?: AbortSignal): Promise<Page<SessionSummary>>;
  session(sessionKey: string, signal?: AbortSignal): Promise<SessionDetail>;
  review: ReviewApi;
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(platformPath(path), {
    credentials: "same-origin",
    ...init,
    headers: { Accept: "application/json", ...init.headers },
  });
  if (!response.ok) throw new FaeWorkbenchApiError(response.status);
  return response.json();
}

const getJson = <T>(path: string, signal?: AbortSignal) => requestJson<T>(path, { signal });

function withoutAgentId(payload: Record<string, unknown>): Record<string, unknown> {
  const { agent_id: _agentId, ...scoped } = payload;
  void _agentId;
  return scoped;
}

function writeJson<T>(path: string, payload: Record<string, unknown>, actor: string, method = "POST"): Promise<T> {
  const identity = actor.trim();
  if (!identity) throw new Error("需要可追责的复审身份");
  return requestJson<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Review-Actor": identity },
    body: JSON.stringify(payload),
  });
}

function sessionPage(value: unknown): Page<SessionSummary> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("FAE session response contract invalid");
  const page = value as Record<string, unknown>;
  if (!Array.isArray(page.items) || !Number.isSafeInteger(page.total) || !Number.isSafeInteger(page.limit)
    || !Number.isSafeInteger(page.offset) || Number(page.total) < 0 || Number(page.limit) < 0 || Number(page.offset) < 0) {
    throw new Error("FAE session response contract invalid");
  }
  return page as unknown as Page<SessionSummary>;
}

function sessionDetail(value: unknown): SessionDetail {
  if (!value || typeof value !== "object" || Array.isArray(value) || !Array.isArray((value as Record<string, unknown>).turns)) {
    throw new Error("FAE session response contract invalid");
  }
  return value as SessionDetail;
}

const QUERY_KEYS = ["q", "channel", "sentiment", "review_status", "outcome", "date_from", "date_to", "limit", "offset"] as const;

export const faeWorkbenchApi: FaeWorkbenchApi = {
  async overview(signal) {
    return parseFaeOverview(await getJson<unknown>("/api/admin/fae/overview", signal));
  },

  async listSessions(query, signal) {
    const params = new URLSearchParams();
    QUERY_KEYS.forEach((key) => {
      const value = query[key];
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    const suffix = params.size ? `?${params}` : "";
    return sessionPage(await getJson<unknown>(`/api/admin/fae/sessions${suffix}`, signal));
  },

  async session(sessionKey, signal) {
    return sessionDetail(await getJson<unknown>(`/api/admin/fae/sessions/${encodeURIComponent(sessionKey)}`, signal));
  },

  review: {
    overview: (signal) => getJson<ReviewOverview>("/api/admin/fae/issue-overview", signal),
    inbox: (signal) => getJson<ReviewInboxItem[]>("/api/admin/fae/issue-inbox?limit=200", signal),
    issues: (signal) => getJson<FeedbackIssueSummary[]>("/api/admin/fae/issues?limit=200", signal),
    turnSummaries(turnKeys, signal) {
      const params = new URLSearchParams();
      turnKeys.forEach((turnKey) => params.append("turn_key", turnKey));
      return getJson<TurnClosureSummary[]>(`/api/admin/fae/turn-summaries?${params}`, signal);
    },
    issue: (id, signal) => getJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}`, signal),
    create: (payload, actor) => writeJson<FeedbackIssueDetail>("/api/admin/fae/issues", withoutAgentId(payload), actor),
    link: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/links`, withoutAgentId(payload), actor),
    update: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}`, payload, actor, "PATCH"),
    move: (issueId, linkId, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(issueId)}/links/${encodeURIComponent(linkId)}/move`, payload, actor),
    fixReady: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/fix-ready`, payload, actor),
    merge: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/merge`, payload, actor),
    addEvidence: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/evidence`, payload, actor),
    verifyEvidence: (evidenceId, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/evidence/${encodeURIComponent(evidenceId)}/verify`, { reason: "machine verification requested" }, actor),
    replay: (issueId, payload, actor) => writeJson<ReplayRun>(`/api/admin/fae/issues/${encodeURIComponent(issueId)}/replays`, payload, actor),
    semanticReview: (replayId, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/replays/${encodeURIComponent(replayId)}/semantic-review`, payload, actor),
    disposition: (issueId, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(issueId)}/disposition`, payload, actor),
  },
};
