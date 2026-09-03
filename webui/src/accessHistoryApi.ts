import { platformPath } from "./auth";

export type AccessEventKind = "login_succeeded" | "page_view";
export type LoginKind = "qr" | "in_client";

export interface AccessHistoryEvent {
  access_event_id: string;
  display_name: string;
  event_kind: AccessEventKind;
  login_kind: LoginKind | null;
  workspace_key: string | null;
  page_key: string | null;
  page_display_name: string | null;
  agent_id: string | null;
  occurred_at: string;
}

export interface AccessHistoryPageResult {
  items: AccessHistoryEvent[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface AccessHistoryFilters {
  date_from?: string;
  date_to?: string;
  display_name?: string;
  workspace_key?: string;
  event_kind?: AccessEventKind;
  limit?: number;
  offset?: number;
}

export class AccessHistoryApiError extends Error {
  constructor(public status: number) { super(`access history API ${status}`); }
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && Object.keys(value).every((key) => keys.includes(key));
}

const EVENT_KEYS = ["access_event_id", "display_name", "event_kind", "login_kind", "workspace_key", "page_key", "page_display_name", "agent_id", "occurred_at"] as const;
const PAGE_KEYS = ["items", "limit", "offset", "has_more"] as const;

function nullableString(value: unknown): value is string | null { return value === null || typeof value === "string"; }

function parseEvent(value: unknown): AccessHistoryEvent {
  if (!object(value) || !exactKeys(value, EVENT_KEYS)
    || typeof value.access_event_id !== "string"
    || typeof value.display_name !== "string"
    || (value.event_kind !== "login_succeeded" && value.event_kind !== "page_view")
    || (value.login_kind !== null && value.login_kind !== "qr" && value.login_kind !== "in_client")
    || !nullableString(value.workspace_key) || !nullableString(value.page_key)
    || !nullableString(value.page_display_name) || !nullableString(value.agent_id)
    || typeof value.occurred_at !== "string" || Number.isNaN(Date.parse(value.occurred_at))) {
    throw new Error("access history response invalid");
  }
  return value as unknown as AccessHistoryEvent;
}

function parsePage(value: unknown): AccessHistoryPageResult {
  if (!object(value) || !exactKeys(value, PAGE_KEYS) || !Array.isArray(value.items)
    || !Number.isInteger(value.limit) || !Number.isInteger(value.offset) || typeof value.has_more !== "boolean") {
    throw new Error("access history response invalid");
  }
  return { items: value.items.map(parseEvent), limit: value.limit as number, offset: value.offset as number, has_more: value.has_more };
}

export async function listAccessEvents(filters: AccessHistoryFilters, signal?: AbortSignal): Promise<AccessHistoryPageResult> {
  const query = new URLSearchParams();
  if (filters.date_from) query.set("date_from", filters.date_from);
  if (filters.date_to) query.set("date_to", filters.date_to);
  if (filters.display_name) query.set("display_name", filters.display_name);
  if (filters.workspace_key) query.set("workspace_key", filters.workspace_key);
  if (filters.event_kind) query.set("event_kind", filters.event_kind);
  query.set("limit", String(filters.limit ?? 50));
  query.set("offset", String(filters.offset ?? 0));
  const response = await fetch(platformPath(`/api/v1/manage/access-events?${query}`), { credentials: "include", signal });
  if (!response.ok) throw new AccessHistoryApiError(response.status);
  return parsePage(await response.json());
}
