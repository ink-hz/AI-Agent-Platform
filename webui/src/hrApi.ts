import { platformPath } from "./auth";
import type { HrPosition, HrPositionDetail, InternalPositionStatus, PositionPage, PositionSource } from "./hrTypes";


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const JOB_ID = /^(?:J[0-9]{4,12}|JOBAD:[0-9]{1,20})$/;
const POSITION_SOURCES = new Set(["official_site", "manual"]);
const OFFICIAL_STATUSES = new Set(["active", "stale", "suspected_inactive", "inactive"]);
const INTERNAL_STATUSES = new Set(["draft", "active", "archived"]);
const POSITION_KEYS = new Set([
  "position_id", "source_kind", "official_job_id", "title", "department", "locations",
  "official_status", "internal_status", "source_version", "row_version", "created_at", "updated_at",
]);


export class HrApiError extends Error {
  constructor(public readonly status: number, public readonly detail: unknown = null) {
    super(`HR API ${status}`);
    this.name = "HrApiError";
  }
}

function object(value: unknown, message = "HR position response invalid"): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(message);
  return value as Record<string, unknown>;
}

function exact(value: Record<string, unknown>, keys: Set<string>): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.size && actual.every((key) => keys.has(key));
}

function id(value: unknown): value is string { return typeof value === "string" && UUID.test(value); }
function text(value: unknown): value is string { return typeof value === "string" && value.length > 0; }
function nullableText(value: unknown): value is string | null { return value === null || text(value); }
function integer(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) >= 0; }
function positive(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) > 0; }
function instant(value: unknown): value is string {
  return text(value) && /^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value));
}
function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(text);
}
function idList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(id) && value.length === new Set(value).size;
}

export function parseHrPosition(value: unknown): HrPosition {
  const raw = object(value);
  if (!exact(raw, POSITION_KEYS) || !id(raw.position_id)
    || !POSITION_SOURCES.has(String(raw.source_kind))
    || !(raw.official_job_id === null || (text(raw.official_job_id) && JOB_ID.test(raw.official_job_id)))
    || !text(raw.title) || !nullableText(raw.department) || !stringList(raw.locations)
    || !(raw.official_status === null || OFFICIAL_STATUSES.has(String(raw.official_status)))
    || !INTERNAL_STATUSES.has(String(raw.internal_status)) || !nullableText(raw.source_version)
    || !positive(raw.row_version) || !instant(raw.created_at) || !instant(raw.updated_at)
    || (raw.source_kind === "official_site" && (raw.official_job_id === null || raw.official_status === null))
    || (raw.source_kind === "manual" && (raw.official_job_id !== null || raw.official_status !== null))) {
    throw new Error("HR position response invalid");
  }
  return {
    positionId: raw.position_id, sourceKind: raw.source_kind as HrPosition["sourceKind"],
    officialJobId: raw.official_job_id, title: raw.title, department: raw.department,
    locations: raw.locations, officialStatus: raw.official_status as HrPosition["officialStatus"],
    internalStatus: raw.internal_status as HrPosition["internalStatus"], sourceVersion: raw.source_version,
    rowVersion: raw.row_version, createdAt: raw.created_at, updatedAt: raw.updated_at,
  };
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(platformPath(path), {
    credentials: "same-origin", ...init,
    headers: { Accept: "application/json", ...init.headers },
  });
  if (!response.ok) {
    let detail: unknown = null;
    try { detail = await response.json(); } catch { /* opaque */ }
    throw new HrApiError(response.status, detail);
  }
  return response.json();
}

function parsePage(value: unknown): PositionPage {
  const raw = object(value);
  if (Object.keys(raw).length !== 2 || !Array.isArray(raw.items) || !nullableText(raw.next_cursor)) {
    throw new Error("HR position response invalid");
  }
  return { items: raw.items.map(parseHrPosition), nextCursor: raw.next_cursor };
}

function parseDetail(value: unknown): HrPositionDetail {
  const raw = object(value);
  const counts = [raw.conversation_count, raw.material_count, raw.artifact_count];
  const detailKeys = new Set(["conversation_count", "material_count", "artifact_count", "conversation_ids", "material_attachment_ids", "artifact_ids", "artifact_attachment_ids"]);
  const base = Object.fromEntries(Object.entries(raw).filter(([key]) => !detailKeys.has(key)));
  if (Object.keys(raw).length !== POSITION_KEYS.size + 7 || counts.some((count) => !integer(count))
    || !idList(raw.conversation_ids) || !idList(raw.material_attachment_ids)
    || !idList(raw.artifact_ids) || !idList(raw.artifact_attachment_ids)) {
    throw new Error("HR position response invalid");
  }
  return {
    ...parseHrPosition(base), conversationCount: Number(raw.conversation_count),
    materialCount: Number(raw.material_count), artifactCount: Number(raw.artifact_count),
    conversationIds: raw.conversation_ids, materialAttachmentIds: raw.material_attachment_ids,
    artifactIds: raw.artifact_ids,
    artifactAttachmentIds: raw.artifact_attachment_ids,
  };
}

export function createHrApi(_csrfToken: string) {
  return {
    async listPositions(filters: { query?: string; source?: PositionSource; internalStatus?: InternalPositionStatus; cursor?: string; limit?: number }, signal?: AbortSignal) {
      const params = new URLSearchParams();
      if (filters.query) params.set("query", filters.query);
      if (filters.source) params.set("source", filters.source);
      if (filters.internalStatus) params.set("internal_status", filters.internalStatus);
      if (filters.cursor) params.set("cursor", filters.cursor);
      if (filters.limit !== undefined) params.set("limit", String(filters.limit));
      return parsePage(await request(`/api/hr/positions${params.size ? `?${params}` : ""}`, { signal }));
    },
    async position(positionId: string, signal?: AbortSignal) {
      return parseDetail(await request(`/api/hr/positions/${encodeURIComponent(positionId)}`, { signal }));
    },
  };
}

export type HrApi = ReturnType<typeof createHrApi>;
