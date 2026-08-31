import { platformPath } from "./auth";
import { parseFaeOverview, type FaeOverview, type FaeSessionQuery } from "./faeWorkbenchTypes";
import type { Page, SessionDetail, SessionSummary } from "./types";


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
}

async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new FaeWorkbenchApiError(response.status);
  return response.json();
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
    return parseFaeOverview(await getJson("/api/admin/fae/overview", signal));
  },

  async listSessions(query, signal) {
    const params = new URLSearchParams();
    QUERY_KEYS.forEach((key) => {
      const value = query[key];
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    const suffix = params.size ? `?${params}` : "";
    return sessionPage(await getJson(`/api/admin/fae/sessions${suffix}`, signal));
  },

  async session(sessionKey, signal) {
    return sessionDetail(await getJson(`/api/admin/fae/sessions/${encodeURIComponent(sessionKey)}`, signal));
  },
};
