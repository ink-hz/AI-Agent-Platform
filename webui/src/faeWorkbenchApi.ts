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
  review(csrfToken: string): ReviewApi;
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

function withoutFaeScope(payload: Record<string, unknown>): Record<string, unknown> {
  const { agent_id: _agentId, source_kind: _sourceKind, ...scoped } = payload;
  void _agentId;
  void _sourceKind;
  return scoped;
}

function writeJson<T>(path: string, payload: Record<string, unknown>, actor: string, csrfToken: string, method = "POST"): Promise<T> {
  const identity = actor.trim();
  if (!identity) throw new Error("需要可追责的复审身份");
  return requestJson<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Review-Actor": identity, "X-CSRF-Token": csrfToken },
    body: JSON.stringify(withoutFaeScope(payload)),
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

const QUERY_KEYS = ["q", "channel", "sentiment", "review_status", "outcome", "date_from", "date_to", "date_before", "subject_key", "has_subject", "abnormal", "has_latency", "limit", "offset"] as const;

const ISSUE_STATUSES = new Set([
  "pending_triage", "fixing", "awaiting_merge", "awaiting_deploy", "awaiting_replay",
  "awaiting_review", "closed", "duplicate", "not_actionable", "wont_fix",
]);

function objectValue(value: unknown, contract: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(contract);
  return value as Record<string, unknown>;
}

function countOrNull(value: unknown): number | null {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function normalizeOverview(value: unknown): ReviewOverview {
  const raw = objectValue(value, "FAE issue overview response contract invalid");
  const rawStatuses = objectValue(raw.statuses ?? {}, "FAE issue overview response contract invalid");
  const rawDispositions = objectValue(raw.dispositions ?? {}, "FAE issue overview response contract invalid");
  const projected = "feedback_totals_status" in raw || Object.keys(rawStatuses).some((status) => !ISSUE_STATUSES.has(status));
  const counts = (source: Record<string, unknown>) => Object.fromEntries(
    Object.entries(source).flatMap(([key, count]) => countOrNull(count) === null ? [] : [[key, Number(count)]]),
  );
  return {
    feedback_rows: countOrNull(raw.feedback_rows),
    negative_rows: countOrNull(raw.negative_rows),
    negative_turns: countOrNull(raw.negative_turns),
    positive_rows: countOrNull(raw.positive_rows),
    issue_total: countOrNull(raw.issue_total),
    statuses: projected ? {} : counts(rawStatuses),
    dispositions: counts(rawDispositions),
    write_available: raw.write_available === true,
    lifecycle_status_available: !projected,
  };
}

function normalizeProgress(value: unknown, issueId: string, projected: boolean) {
  const raw = objectValue(value, "FAE issue progress response contract invalid");
  const status = typeof raw.status === "string" && ISSUE_STATUSES.has(raw.status) ? raw.status : "unknown";
  return {
    issue_id: stringOrNull(raw.issue_id) ?? issueId,
    status: projected ? "unknown" as const : status as FeedbackIssueSummary["progress"]["status"],
    missing_gates: projected ? null : Array.isArray(raw.missing_gates) ? raw.missing_gates.filter((gate): gate is string => typeof gate === "string") : null,
    replay_passed_turns: projected ? null : countOrNull(raw.replay_passed_turns),
    replay_required_turns: projected ? null : countOrNull(raw.replay_required_turns),
    reopened: projected ? null : typeof raw.reopened === "boolean" ? raw.reopened : null,
  };
}

function normalizeIssue(value: unknown): FeedbackIssueSummary {
  const raw = objectValue(value, "FAE issue response contract invalid");
  const id = stringOrNull(raw.id);
  const agentId = stringOrNull(raw.agent_id);
  const title = stringOrNull(raw.title);
  if (!id || !agentId || title === null) throw new Error("FAE issue response contract invalid");
  const projected = raw.replica_read_only === true;
  const disposition = raw.disposition === "duplicate" || raw.disposition === "not_actionable" || raw.disposition === "wont_fix"
    ? raw.disposition : "actionable";
  const priority = raw.priority === "P0" || raw.priority === "P1" || raw.priority === "P2" || raw.priority === "P3" ? raw.priority : "P2";
  return {
    id,
    agent_id: agentId,
    origin_turn_key: stringOrNull(raw.origin_turn_key),
    title,
    priority,
    failure_layer: stringOrNull(raw.failure_layer),
    secondary_layers: Array.isArray(raw.secondary_layers) ? raw.secondary_layers.filter((layer): layer is string => typeof layer === "string") : [],
    root_cause: projected ? null : stringOrNull(raw.root_cause) ?? "",
    impact_scope: projected ? null : stringOrNull(raw.impact_scope) ?? "",
    owner: stringOrNull(raw.owner),
    disposition,
    row_version: projected ? null : countOrNull(raw.row_version),
    ...(typeof raw.created_at === "string" ? { created_at: raw.created_at } : {}),
    ...(typeof raw.updated_at === "string" ? { updated_at: raw.updated_at } : {}),
    progress: normalizeProgress(raw.progress, id, projected),
  };
}

function normalizeIssues(value: unknown): { items: FeedbackIssueSummary[]; total: number; limit: number; offset: number; has_more: boolean } {
  if (Array.isArray(value)) {
    return { items: value.map(normalizeIssue), total: value.length,
      limit: value.length || 200, offset: 0, has_more: false };
  }
  const raw = objectValue(value, "FAE issues response contract invalid");
  if (!Array.isArray(raw.items) || countOrNull(raw.total) === null || countOrNull(raw.limit) === null || countOrNull(raw.offset) === null) {
    throw new Error("FAE issues response contract invalid");
  }
  return {
    items: raw.items.map(normalizeIssue), total: Number(raw.total), limit: Number(raw.limit),
    offset: Number(raw.offset), has_more: raw.has_more === true,
  };
}

function normalizeInbox(value: unknown): ReviewInboxItem[] {
  if (!Array.isArray(value)) throw new Error("FAE issue inbox response contract invalid");
  return value.map((item) => {
    const raw = objectValue(item, "FAE issue inbox response contract invalid");
    const agentId = stringOrNull(raw.agent_id);
    const turnKey = stringOrNull(raw.turn_key);
    const firstFeedbackAt = stringOrNull(raw.first_feedback_at);
    if (!agentId || !turnKey || !firstFeedbackAt) throw new Error("FAE issue inbox response contract invalid");
    return {
      agent_id: agentId,
      turn_key: turnKey,
      question: stringOrNull(raw.question) ?? "",
      answer: stringOrNull(raw.answer) ?? "",
      feedback_keys: Array.isArray(raw.feedback_keys) ? raw.feedback_keys.filter((key): key is string => typeof key === "string") : [],
      ...(countOrNull(raw.feedback_count) === null ? {} : { feedback_count: Number(raw.feedback_count) }),
      first_feedback_at: firstFeedbackAt,
    };
  });
}

function normalizeDetail(value: unknown): FeedbackIssueDetail {
  const raw = objectValue(value, "FAE issue detail response contract invalid");
  const rawIssue = objectValue(raw.issue, "FAE issue detail response contract invalid");
  const issue = normalizeIssue({
    ...rawIssue,
    progress: raw.progress,
    replica_read_only: raw.replica_read_only === true || rawIssue.replica_read_only === true,
  });
  const arrays = ["links", "evidence", "replays", "events"] as const;
  if (arrays.some((key) => raw[key] !== null && !Array.isArray(raw[key]))) throw new Error("FAE issue detail response contract invalid");
  const projected = raw.replica_read_only === true || rawIssue.replica_read_only === true;
  return {
    issue: (({ progress: _progress, ...summary }) => summary)(issue),
    links: (raw.links ?? []) as FeedbackIssueDetail["links"],
    evidence: (raw.evidence ?? []) as FeedbackIssueDetail["evidence"],
    replays: (raw.replays ?? []) as FeedbackIssueDetail["replays"],
    events: (raw.events ?? []) as FeedbackIssueDetail["events"],
    section_availability: objectValue(raw.availability ?? raw.section_availability ?? {}, "FAE issue detail response contract invalid") as FeedbackIssueDetail["section_availability"],
    progress: normalizeProgress(raw.progress, issue.id, projected),
  };
}

function reviewApi(csrfToken: string): ReviewApi {
  return {
    overview: async (signal) => normalizeOverview(await getJson<unknown>("/api/admin/fae/issue-overview", signal)),
    inbox: async (signal) => normalizeInbox(await getJson<unknown>("/api/admin/fae/issue-inbox?limit=200", signal)),
    issues: async (signal, filters) => {
      const params = new URLSearchParams({ limit: String(filters?.limit ?? 200) });
      if ((filters?.offset ?? 0) > 0) params.set("offset", String(filters?.offset));
      if (filters?.status) params.set("status", filters.status);
      if (filters?.disposition) params.set("disposition", filters.disposition);
      if (filters?.priority) params.set("priority", filters.priority);
      if (filters?.failure_layer) params.set("failure_layer", filters.failure_layer);
      if (filters?.owner) params.set("owner", filters.owner);
      if (filters?.query) params.set("q", filters.query);
      if (filters?.created_after) params.set("created_after", filters.created_after);
      return normalizeIssues(await getJson<unknown>(`/api/admin/fae/issues?${params}`, signal));
    },
    turnSummaries(turnKeys, signal) {
      const params = new URLSearchParams();
      turnKeys.forEach((turnKey) => params.append("turn_key", turnKey));
      return getJson<TurnClosureSummary[]>(`/api/admin/fae/turn-summaries?${params}`, signal);
    },
    issue: async (id, signal) => normalizeDetail(await getJson<unknown>(`/api/admin/fae/issues/${encodeURIComponent(id)}`, signal)),
    create: (payload, actor) => writeJson<FeedbackIssueDetail>("/api/admin/fae/issues", payload, actor, csrfToken),
    link: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/links`, payload, actor, csrfToken),
    update: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}`, payload, actor, csrfToken, "PATCH"),
    move: (issueId, linkId, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(issueId)}/links/${encodeURIComponent(linkId)}/move`, payload, actor, csrfToken),
    fixReady: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/fix-ready`, payload, actor, csrfToken),
    merge: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/merge`, payload, actor, csrfToken),
    addEvidence: (id, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(id)}/evidence`, payload, actor, csrfToken),
    verifyEvidence: (evidenceId, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/evidence/${encodeURIComponent(evidenceId)}/verify`, { reason: "machine verification requested" }, actor, csrfToken),
    replay: (issueId, payload, actor) => writeJson<ReplayRun>(`/api/admin/fae/issues/${encodeURIComponent(issueId)}/replays`, payload, actor, csrfToken),
    semanticReview: (replayId, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/replays/${encodeURIComponent(replayId)}/semantic-review`, payload, actor, csrfToken),
    disposition: (issueId, payload, actor) => writeJson<FeedbackIssueDetail>(`/api/admin/fae/issues/${encodeURIComponent(issueId)}/disposition`, payload, actor, csrfToken),
  };
}

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

  review: reviewApi,
};
