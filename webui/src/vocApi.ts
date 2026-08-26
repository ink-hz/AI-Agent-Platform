import { platformPath } from "./auth";

export type EvidenceBasis = "customer_quote" | "employee_observation" | "employee_relay" | "unknown";
export type DraftState = "collecting" | "committed" | "cancelled";

export interface VocDraftContent {
  customer: string | null;
  feedback: string;
  product_or_scenario: string | null;
  impact: string | null;
  evidence_basis: EvidenceBasis;
  gaps: string[];
}

export interface VocDraft {
  draft_id: string;
  state: DraftState;
  version: number;
  source_text: string;
  content: VocDraftContent;
  submitted_voc_no: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreatedVocDraft extends VocDraft { assistant_message: string }
export interface VocSubmission { voc_no: string; revision: number; already_submitted: boolean }
export interface VocSummary { voc_no: string; revision: number; latest_content: string; created_at: string; updated_at: string }
export interface VocEntry { revision: number; entry_type: "original" | "supplement" | "correction"; content: string; created_at: string }
export interface VocDetail { voc_no: string; revision: number; created_at: string; updated_at: string; entries: VocEntry[] }
export interface VocMutation { voc_no: string; revision: number }

export class VocApiError extends Error {
  constructor(public status: number, public code: string) {
    super(`VOC API ${status}: ${code}`);
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && Object.keys(value).every((key) => keys.includes(key));
}

function string(value: unknown): value is string { return typeof value === "string" && value.length > 0; }
function nullableString(value: unknown): value is string | null { return value === null || typeof value === "string"; }
function positive(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) > 0; }

const CONTENT_KEYS = ["customer", "feedback", "product_or_scenario", "impact", "evidence_basis", "gaps"];
const DRAFT_KEYS = ["draft_id", "state", "version", "source_text", "content", "submitted_voc_no", "created_at", "updated_at"];

function parseContent(value: unknown): VocDraftContent {
  if (!object(value) || !exact(value, CONTENT_KEYS)
    || !nullableString(value.customer) || !string(value.feedback)
    || !nullableString(value.product_or_scenario) || !nullableString(value.impact)
    || !["customer_quote", "employee_observation", "employee_relay", "unknown"].includes(String(value.evidence_basis))
    || !Array.isArray(value.gaps) || value.gaps.some((gap) => !string(gap))) {
    throw new Error("VOC draft response invalid");
  }
  return { ...value, gaps: [...value.gaps] } as VocDraftContent;
}

function parseDraft(value: unknown): VocDraft {
  if (!object(value) || !exact(value, DRAFT_KEYS)
    || !string(value.draft_id) || !["collecting", "committed", "cancelled"].includes(String(value.state))
    || !positive(value.version) || !string(value.source_text)
    || !nullableString(value.submitted_voc_no) || !string(value.created_at) || !string(value.updated_at)) {
    throw new Error("VOC draft response invalid");
  }
  return { ...value, content: parseContent(value.content) } as VocDraft;
}

function parseCreatedDraft(value: unknown): CreatedVocDraft {
  if (!object(value) || !exact(value, [...DRAFT_KEYS, "assistant_message"]) || !string(value.assistant_message)) {
    throw new Error("VOC draft response invalid");
  }
  const { assistant_message, ...draft } = value;
  return { ...parseDraft(draft), assistant_message };
}

function parseSubmission(value: unknown): VocSubmission {
  if (!object(value) || !exact(value, ["voc_no", "revision", "already_submitted"])
    || !string(value.voc_no) || !positive(value.revision) || typeof value.already_submitted !== "boolean") {
    throw new Error("VOC submission response invalid");
  }
  return value as unknown as VocSubmission;
}

function parseSummary(value: unknown): VocSummary {
  if (!object(value) || !exact(value, ["voc_no", "revision", "latest_content", "created_at", "updated_at"])
    || !string(value.voc_no) || !positive(value.revision) || !string(value.latest_content)
    || !string(value.created_at) || !string(value.updated_at)) throw new Error("VOC list response invalid");
  return value as unknown as VocSummary;
}

function parseList(value: unknown): VocSummary[] {
  if (!object(value) || !exact(value, ["items"]) || !Array.isArray(value.items)) throw new Error("VOC list response invalid");
  return value.items.map(parseSummary);
}

function parseDetail(value: unknown): VocDetail {
  if (!object(value) || !exact(value, ["voc_no", "revision", "created_at", "updated_at", "entries"])
    || !string(value.voc_no) || !positive(value.revision) || !string(value.created_at)
    || !string(value.updated_at) || !Array.isArray(value.entries)) throw new Error("VOC detail response invalid");
  const entries = value.entries.map((entry) => {
    if (!object(entry) || !exact(entry, ["revision", "entry_type", "content", "created_at"])
      || !positive(entry.revision) || !["original", "supplement", "correction"].includes(String(entry.entry_type))
      || !string(entry.content) || !string(entry.created_at)) throw new Error("VOC detail response invalid");
    return entry as unknown as VocEntry;
  });
  return { ...value, entries } as VocDetail;
}

function parseMutation(value: unknown): VocMutation {
  if (!object(value) || !exact(value, ["voc_no", "revision"]) || !string(value.voc_no) || !positive(value.revision)) {
    throw new Error("VOC mutation response invalid");
  }
  return value as unknown as VocMutation;
}

async function body(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { throw new Error("VOC response invalid"); }
}

async function checked(response: Response): Promise<unknown> {
  const value = await body(response);
  if (!response.ok) {
    const code = object(value) && typeof value.detail === "string" ? value.detail : "request_failed";
    throw new VocApiError(response.status, code);
  }
  return value;
}

function mutation(method: "POST" | "PATCH", csrfToken: string, value: object): RequestInit {
  return {
    method, credentials: "include", body: JSON.stringify(value),
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
  };
}

export interface VocApi {
  activeDraft(): Promise<VocDraft | null>;
  createDraft(requestId: string, sourceText: string, csrfToken: string): Promise<CreatedVocDraft>;
  updateDraft(draftId: string, requestId: string, expectedVersion: number, content: VocDraftContent, csrfToken: string): Promise<VocDraft>;
  cancelDraft(draftId: string, requestId: string, expectedVersion: number, csrfToken: string): Promise<VocDraft>;
  submitDraft(draftId: string, requestId: string, expectedVersion: number, csrfToken: string): Promise<VocSubmission>;
  listVocs(query?: string): Promise<VocSummary[]>;
  getVoc(vocNo: string): Promise<VocDetail>;
  supplementVoc(vocNo: string, requestId: string, content: string, csrfToken: string): Promise<VocMutation>;
}

export function createVocApi(): VocApi {
  const prefix = platformPath("/api/v1/extensions/voc");
  return {
    async activeDraft() {
      const value = await checked(await fetch(`${prefix}/drafts/active`, { credentials: "include" }));
      return value === null ? null : parseDraft(value);
    },
    async createDraft(requestId, sourceText, csrfToken) {
      const response = await fetch(`${prefix}/drafts`, mutation("POST", csrfToken, { request_id: requestId, source_text: sourceText }));
      return parseCreatedDraft(await checked(response));
    },
    async updateDraft(draftId, requestId, expectedVersion, content, csrfToken) {
      const response = await fetch(`${prefix}/drafts/${encodeURIComponent(draftId)}`, mutation("PATCH", csrfToken, { request_id: requestId, expected_version: expectedVersion, content }));
      return parseDraft(await checked(response));
    },
    async cancelDraft(draftId, requestId, expectedVersion, csrfToken) {
      const response = await fetch(`${prefix}/drafts/${encodeURIComponent(draftId)}/cancel`, mutation("POST", csrfToken, { request_id: requestId, expected_version: expectedVersion }));
      return parseDraft(await checked(response));
    },
    async submitDraft(draftId, requestId, expectedVersion, csrfToken) {
      const response = await fetch(`${prefix}/drafts/${encodeURIComponent(draftId)}/submit`, mutation("POST", csrfToken, { request_id: requestId, expected_version: expectedVersion }));
      return parseSubmission(await checked(response));
    },
    async listVocs(query) {
      const params = new URLSearchParams({ limit: "20" });
      if (query) params.set("query", query);
      return parseList(await checked(await fetch(`${prefix}/vocs?${params}`, { credentials: "include" })));
    },
    async getVoc(vocNo) {
      return parseDetail(await checked(await fetch(`${prefix}/vocs/${encodeURIComponent(vocNo)}`, { credentials: "include" })));
    },
    async supplementVoc(vocNo, requestId, content, csrfToken) {
      const response = await fetch(`${prefix}/vocs/${encodeURIComponent(vocNo)}/supplements`, mutation("POST", csrfToken, { request_id: requestId, content }));
      return parseMutation(await checked(response));
    },
  };
}

export const vocApi = createVocApi();
