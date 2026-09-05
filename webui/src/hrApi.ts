import { platformPath } from "./auth";
import type {
  HrConfirmedPositionPackage, HrPosition, HrPositionDetail, HrPositionDraft,
  HrPositionPackage, HrPositionPackageModules, InternalPositionStatus,
  PositionConversationBinding, PositionMaterial, PositionPage, PositionSource,
  ProposePositionDraftInput,
} from "./hrTypes";


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const JOB_ID = /^(?:J[0-9]{4,12}|JOBAD:[0-9]{1,20})$/;
const POSITION_SOURCES = new Set(["official_site", "manual"]);
const OFFICIAL_STATUSES = new Set(["active", "stale", "suspected_inactive", "inactive"]);
const INTERNAL_STATUSES = new Set(["draft", "active", "archived"]);
const DRAFT_SOURCES = new Set(["historical_conversation", "new_conversation"]);
const DRAFT_STATES = new Set(["proposed", "confirmed", "merged", "dismissed"]);
const BINDING_KINDS = new Set([
  "created_in_position", "draft_confirmed", "draft_merged", "historical_exact", "manual_correction",
]);
const POSITION_KEYS = new Set([
  "position_id", "source_kind", "official_job_id", "title", "department", "locations",
  "official_status", "internal_status", "source_version", "row_version", "created_at", "updated_at",
]);
const DRAFT_KEYS = new Set([
  "draft_id", "source_kind", "source_key", "source_conversation_id", "title", "proposal",
  "evidence", "discovery_rule_version", "state", "resolved_position_id", "row_version",
  "created_at", "updated_at",
]);
const POSITION_PACKAGE_KEYS = new Set([
  "draft_id", "draft_version_id", "conversation_id", "version_number", "title",
  "modules", "row_version", "created_at", "updated_at",
]);
const CONFIRMED_POSITION_PACKAGE_KEYS = new Set([
  "position_id", "context_version_id", "conversation_id",
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
function nullableId(value: unknown): value is string | null { return value === null || id(value); }
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

function parseDraft(value: unknown): HrPositionDraft {
  const raw = object(value, "HR position draft response invalid");
  if (!exact(raw, DRAFT_KEYS) || !id(raw.draft_id) || !DRAFT_SOURCES.has(String(raw.source_kind))
    || !text(raw.source_key) || !nullableId(raw.source_conversation_id) || !text(raw.title)
    || !raw.proposal || typeof raw.proposal !== "object" || Array.isArray(raw.proposal)
    || !raw.evidence || typeof raw.evidence !== "object" || Array.isArray(raw.evidence)
    || !text(raw.discovery_rule_version) || !DRAFT_STATES.has(String(raw.state))
    || !nullableId(raw.resolved_position_id) || !positive(raw.row_version)
    || !instant(raw.created_at) || !instant(raw.updated_at)
    || (["confirmed", "merged"].includes(String(raw.state)) !== (raw.resolved_position_id !== null))) {
    throw new Error("HR position draft response invalid");
  }
  return {
    draftId: raw.draft_id, sourceKind: raw.source_kind as HrPositionDraft["sourceKind"],
    sourceKey: raw.source_key, sourceConversationId: raw.source_conversation_id,
    title: raw.title, proposal: raw.proposal as Record<string, unknown>,
    evidence: raw.evidence as Record<string, unknown>, discoveryRuleVersion: raw.discovery_rule_version,
    state: raw.state as HrPositionDraft["state"], resolvedPositionId: raw.resolved_position_id,
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

function mutation(csrfToken: string, requestId: string, method: string, body?: unknown): RequestInit {
  if (!id(requestId)) throw new Error("HR request id invalid");
  return {
    method,
    headers: {
      "Content-Type": "application/json", "X-CSRF-Token": csrfToken,
      "Idempotency-Key": requestId,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
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

function parseMaterial(value: unknown): PositionMaterial {
  const raw = object(value);
  if (Object.keys(raw).length !== 5 || !id(raw.position_id) || !id(raw.attachment_id)
    || typeof raw.active !== "boolean" || !instant(raw.created_at) || !instant(raw.updated_at)) {
    throw new Error("HR position material response invalid");
  }
  return { positionId: raw.position_id, attachmentId: raw.attachment_id, active: raw.active,
    createdAt: raw.created_at, updatedAt: raw.updated_at };
}

function parseBinding(value: unknown): PositionConversationBinding {
  const raw = object(value);
  if (Object.keys(raw).length !== 5 || !id(raw.position_id) || !id(raw.conversation_id)
    || !BINDING_KINDS.has(String(raw.binding_kind)) || !nullableId(raw.previous_position_id)
    || !instant(raw.created_at)) throw new Error("HR position binding response invalid");
  return { positionId: raw.position_id, conversationId: raw.conversation_id,
    bindingKind: raw.binding_kind as PositionConversationBinding["bindingKind"],
    previousPositionId: raw.previous_position_id, createdAt: raw.created_at };
}

function packageIdentifier(value: unknown): string {
  if (!id(value)) throw new Error("HR position package identifier invalid");
  return value;
}

function packageModules(value: unknown): HrPositionPackageModules {
  const raw = object(value, "HR position package response invalid");
  const keys = new Set(["mission", "jd", "jr"]);
  if (!exact(raw, keys)) throw new Error("HR position package response invalid");
  const module = (name: "mission" | "jd" | "jr") => {
    const selected = object(raw[name], "HR position package response invalid");
    if (!exact(selected, new Set(["text"])) || !text(selected.text)) {
      throw new Error("HR position package response invalid");
    }
    return { text: selected.text };
  };
  return { mission: module("mission"), jd: module("jd"), jr: module("jr") };
}

function parsePositionPackage(value: unknown): HrPositionPackage {
  const raw = object(value, "HR position package response invalid");
  if (!exact(raw, POSITION_PACKAGE_KEYS) || !id(raw.draft_id)
    || !id(raw.draft_version_id) || !id(raw.conversation_id)
    || !positive(raw.version_number) || !text(raw.title)
    || !positive(raw.row_version) || !instant(raw.created_at)
    || !instant(raw.updated_at)) {
    throw new Error("HR position package response invalid");
  }
  return {
    draftId: raw.draft_id, draftVersionId: raw.draft_version_id,
    conversationId: raw.conversation_id, versionNumber: raw.version_number,
    title: raw.title, modules: packageModules(raw.modules),
    rowVersion: raw.row_version, createdAt: raw.created_at, updatedAt: raw.updated_at,
  };
}

function parseConfirmedPositionPackage(value: unknown): HrConfirmedPositionPackage {
  const raw = object(value, "HR confirmed position package response invalid");
  if (!exact(raw, CONFIRMED_POSITION_PACKAGE_KEYS) || !id(raw.position_id)
    || !id(raw.context_version_id) || !id(raw.conversation_id)) {
    throw new Error("HR confirmed position package response invalid");
  }
  return {
    positionId: raw.position_id, contextVersionId: raw.context_version_id,
    conversationId: raw.conversation_id,
  };
}

export function createHrApi(csrfToken: string) {
  const write = (
    path: string, requestId: string, body: unknown, method = "POST",
    signal?: AbortSignal,
  ) => request(path, { ...mutation(csrfToken, requestId, method, body), signal });
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
    async positionPackage(conversationId: string, signal?: AbortSignal): Promise<HrPositionPackage> {
      const selected = packageIdentifier(conversationId);
      return parsePositionPackage(await request(
        `/api/hr/conversations/${encodeURIComponent(selected)}/position-package`,
        { signal },
      ));
    },
    async listDrafts(state?: HrPositionDraft["state"], signal?: AbortSignal) {
      const raw = object(
        await request(`/api/hr/position-drafts${state ? `?state=${encodeURIComponent(state)}` : ""}`, { signal }),
        "HR position draft response invalid",
      );
      if (!exact(raw, new Set(["items"])) || !Array.isArray(raw.items)) {
        throw new Error("HR position draft response invalid");
      }
      return raw.items.map(parseDraft);
    },
    async proposeDraft(input: ProposePositionDraftInput, requestId: string) {
      return parseDraft(await write("/api/hr/position-drafts", requestId, {
        source_kind: input.sourceKind, source_key: input.sourceKey,
        source_conversation_id: input.sourceConversationId, title: input.title,
        proposal: input.proposal, evidence: input.evidence,
        discovery_rule_version: input.discoveryRuleVersion,
      }));
    },
    async confirmDraft(draftId: string, version: number, requestId: string) {
      return parseHrPosition(await write(`/api/hr/position-drafts/${encodeURIComponent(draftId)}/confirm`, requestId, { expected_row_version: version }));
    },
    async confirmPositionPackage(
      draftId: string,
      draftVersionId: string,
      expectedRowVersion: number,
      requestId: string,
      signal?: AbortSignal,
    ): Promise<HrConfirmedPositionPackage> {
      const selectedDraft = packageIdentifier(draftId);
      const selectedVersion = packageIdentifier(draftVersionId);
      if (!positive(expectedRowVersion)) {
        throw new Error("HR position package row version invalid");
      }
      return parseConfirmedPositionPackage(await write(
        `/api/hr/position-drafts/${encodeURIComponent(selectedDraft)}`
        + `/versions/${encodeURIComponent(selectedVersion)}/confirm`,
        requestId,
        { expected_row_version: expectedRowVersion },
        "POST",
        signal,
      ));
    },
    async mergeDraft(draftId: string, targetPositionId: string, version: number, requestId: string) {
      return parseDraft(await write(`/api/hr/position-drafts/${encodeURIComponent(draftId)}/merge`, requestId, { target_position_id: targetPositionId, expected_row_version: version }));
    },
    async dismissDraft(draftId: string, version: number, requestId: string) {
      return parseDraft(await write(`/api/hr/position-drafts/${encodeURIComponent(draftId)}/dismiss`, requestId, { expected_row_version: version }));
    },
    async bindConversation(positionId: string, conversationId: string, requestId: string) {
      return parseBinding(await write(`/api/hr/positions/${encodeURIComponent(positionId)}/conversations/${encodeURIComponent(conversationId)}`, requestId, {}));
    },
    async promoteMaterial(positionId: string, attachmentId: string, requestId: string) {
      return parseMaterial(await write(`/api/hr/positions/${encodeURIComponent(positionId)}/materials/${encodeURIComponent(attachmentId)}`, requestId, {}));
    },
    async removeMaterial(positionId: string, attachmentId: string, requestId: string) {
      return parseMaterial(await write(`/api/hr/positions/${encodeURIComponent(positionId)}/materials/${encodeURIComponent(attachmentId)}`, requestId, undefined, "DELETE"));
    },
  };
}

export type HrApi = ReturnType<typeof createHrApi>;
