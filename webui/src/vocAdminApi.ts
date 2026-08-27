import { platformPath } from "./auth";

export type VocAnalysisStatus = "pending" | "claimed" | "succeeded" | "failed" | "not_requested";
export type VocSource = "platform" | "dingtalk";
export type VocEntryType = "original" | "supplement" | "correction";

export interface VocAdminSummary {
  voc_no: string;
  submitter_internal_user_id: string | null;
  submitter_name: string;
  source: VocSource;
  latest_content: string;
  revision: number;
  analysis_status: VocAnalysisStatus;
  created_at: string;
  updated_at: string;
}

export interface VocAdminEntry {
  revision: number;
  entry_type: VocEntryType;
  content: string;
  created_at: string;
}

export interface VocAdminDetail extends VocAdminSummary {
  entries: VocAdminEntry[];
}

export interface VocAdminPage {
  items: VocAdminSummary[];
  next_cursor: string | null;
}

export interface VocSubmitterOption {
  internal_user_id: string;
  display_name: string;
}

export interface VocAdminFilters {
  query?: string | null;
  submitterInternalUserId?: string | null;
  legacySubmitterName?: string | null;
  createdFrom?: string | null;
  createdTo?: string | null;
  cursor?: string | null;
  limit?: number;
}

export interface VocAdminApi {
  list(filters: VocAdminFilters, signal: AbortSignal): Promise<VocAdminPage>;
  detail(vocNo: string, signal: AbortSignal): Promise<VocAdminDetail>;
  submitters(signal: AbortSignal): Promise<VocSubmitterOption[]>;
}

export class VocAdminApiError extends Error {
  constructor(public status: number, public code: string) {
    super(`VOC management API ${status}: ${code}`);
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function nonempty(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function positive(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function uuid(value: unknown): value is string {
  return typeof value === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function isoTimestamp(value: unknown): value is string {
  return typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

const SUMMARY_KEYS = [
  "voc_no", "submitter_internal_user_id", "submitter_name", "source",
  "latest_content", "revision", "analysis_status", "created_at", "updated_at",
] as const;
const ANALYSIS_STATUSES = ["pending", "claimed", "succeeded", "failed", "not_requested"];

function summaryValid(value: Record<string, unknown>): boolean {
  return /^VOC-\d{8}-\d{3,}$/.test(String(value.voc_no))
    && (value.submitter_internal_user_id === null || uuid(value.submitter_internal_user_id))
    && nonempty(value.submitter_name)
    && ["platform", "dingtalk"].includes(String(value.source))
    && nonempty(value.latest_content)
    && positive(value.revision)
    && ANALYSIS_STATUSES.includes(String(value.analysis_status))
    && isoTimestamp(value.created_at)
    && isoTimestamp(value.updated_at);
}

function parseSummary(value: unknown, message: string): VocAdminSummary {
  if (!object(value) || !exact(value, SUMMARY_KEYS) || !summaryValid(value)) {
    throw new Error(message);
  }
  return value as unknown as VocAdminSummary;
}

function parsePage(value: unknown): VocAdminPage {
  if (!object(value) || !exact(value, ["items", "next_cursor"])
    || !Array.isArray(value.items)
    || !(value.next_cursor === null || nonempty(value.next_cursor))) {
    throw new Error("VOC management list response invalid");
  }
  return {
    items: value.items.map((item) => parseSummary(item, "VOC management list response invalid")),
    next_cursor: value.next_cursor,
  };
}

function parseDetail(value: unknown): VocAdminDetail {
  const keys = [...SUMMARY_KEYS, "entries"];
  if (!object(value) || !exact(value, keys) || !summaryValid(value)
    || !Array.isArray(value.entries)) {
    throw new Error("VOC management detail response invalid");
  }
  const entries = value.entries.map((entry) => {
    if (!object(entry) || !exact(entry, ["revision", "entry_type", "content", "created_at"])
      || !positive(entry.revision)
      || !["original", "supplement", "correction"].includes(String(entry.entry_type))
      || !nonempty(entry.content) || !isoTimestamp(entry.created_at)) {
      throw new Error("VOC management detail response invalid");
    }
    return entry as unknown as VocAdminEntry;
  });
  return { ...value, entries } as unknown as VocAdminDetail;
}

function parseSubmitters(value: unknown): VocSubmitterOption[] {
  if (!object(value) || !exact(value, ["items"]) || !Array.isArray(value.items)) {
    throw new Error("VOC submitter response invalid");
  }
  return value.items.map((item) => {
    if (!object(item) || !exact(item, ["internal_user_id", "display_name"])
      || !uuid(item.internal_user_id) || !nonempty(item.display_name)) {
      throw new Error("VOC submitter response invalid");
    }
    return item as unknown as VocSubmitterOption;
  });
}

async function checked(response: Response): Promise<unknown> {
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new Error("VOC management response invalid");
  }
  if (!response.ok) {
    const code = object(value) && typeof value.detail === "string"
      ? value.detail
      : "request_failed";
    throw new VocAdminApiError(response.status, code);
  }
  return value;
}

function read(path: string, signal: AbortSignal): Promise<unknown> {
  return fetch(platformPath(path), { credentials: "include", signal }).then(checked);
}

export function createVocAdminApi(): VocAdminApi {
  const prefix = "/api/v1/extensions/voc/admin";
  return {
    async list(filters, signal) {
      const params = new URLSearchParams();
      const values: Array<[string, string | number | null | undefined]> = [
        ["query", filters.query],
        ["submitter_internal_user_id", filters.submitterInternalUserId],
        ["legacy_submitter_name", filters.legacySubmitterName],
        ["created_from", filters.createdFrom],
        ["created_to", filters.createdTo],
        ["cursor", filters.cursor],
        ["limit", filters.limit],
      ];
      values.forEach(([key, value]) => {
        if (value !== null && value !== undefined && value !== "") {
          params.set(key, String(value));
        }
      });
      const suffix = params.size > 0 ? `?${params}` : "";
      return parsePage(await read(`${prefix}/vocs${suffix}`, signal));
    },
    async detail(vocNo, signal) {
      return parseDetail(await read(`${prefix}/vocs/${encodeURIComponent(vocNo)}`, signal));
    },
    async submitters(signal) {
      return parseSubmitters(await read(`${prefix}/submitters`, signal));
    },
  };
}

export const vocAdminApi = createVocAdminApi();
