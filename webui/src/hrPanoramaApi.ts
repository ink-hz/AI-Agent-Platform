import { platformPath } from "./auth";
import type {
  AddHrPanoramaCompanyInput, HrPanoramaFact, HrPanoramaInference, HrPanoramaInsight,
  HrPanoramaReport, HrPanoramaRun, HrPanoramaSnapshot, HrPanoramaSource,
  StartHrPanoramaRunInput,
} from "./hrPanoramaTypes";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;
const ERROR_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const RUN_STATES = new Set(["queued", "running", "completed", "partially_completed", "failed"]);
const JOB_STATES = new Set(["open", "closed", "unknown"]);
const SOURCE_KEYS = new Set(["source_id", "source_kind", "canonical_name", "aliases", "approved_urls", "active", "created_at", "updated_at"]);
const RUN_KEYS = new Set(["run_id", "selected_source_ids", "conversation_id", "state", "error_code", "source_failures", "row_version", "started_at", "finished_at", "created_at", "updated_at"]);
const FACT_KEYS = new Set(["fact_id", "text", "snapshot_id", "observation_id", "source_url", "observed_at"]);
const INFERENCE_KEYS = new Set(["text", "basis_fact_ids"]);
const UNKNOWN_KEYS = new Set(["text"]);
const INSIGHT_KEYS = new Set(["insight_version_id", "run_id", "version_number", "selected_source_ids", "snapshot_ids", "facts", "inferences", "unknowns", "direction_clusters", "summary", "source_conversation_id", "source_turn_id", "agent_id", "model_version", "created_at"]);
const SNAPSHOT_KEYS = new Set(["snapshot_id", "run_id", "source_id", "public_job_key", "title", "location", "duty_excerpt", "requirement_excerpt", "source_url", "observed_at", "content_sha256", "status", "created_at"]);

export class HrPanoramaApiError extends Error {
  constructor(public readonly status: number) {
    super(`HR Panorama API ${status}`);
    this.name = "HrPanoramaApiError";
  }
}

function invalid(): never { throw new Error("HR Panorama response invalid"); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: ReadonlySet<string>): void {
  const actual = Object.keys(value);
  if (actual.length !== keys.size || actual.some((key) => !keys.has(key))) invalid();
}
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) invalid();
  return value;
}
function inputId(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) throw new Error("HR Panorama identifier invalid");
  return value;
}
function text(value: unknown, maximum = 32768): string {
  if (typeof value !== "string" || !value.trim() || value.includes("\0") || value.length > maximum) invalid();
  return value;
}
function timestamp(value: unknown): string {
  const selected = text(value, 64);
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(selected) || !Number.isFinite(Date.parse(selected))) invalid();
  return selected;
}
function nullableTimestamp(value: unknown): string | null { return value === null ? null : timestamp(value); }
function positive(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) invalid();
  return Number(value);
}
function stringList(value: unknown, maximum = 1000): string[] {
  if (!Array.isArray(value) || value.length > maximum) invalid();
  const values = value.map((item) => text(item, 2048));
  if (new Set(values).size !== values.length) invalid();
  return values;
}
function idList(value: unknown, maximum = 1000): string[] {
  if (!Array.isArray(value) || value.length > maximum) invalid();
  const values = value.map(id);
  if (new Set(values).size !== values.length) invalid();
  return values;
}
function httpsUrl(value: unknown): string {
  const selected = text(value, 2048);
  let parsed: URL;
  try { parsed = new URL(selected); } catch { invalid(); }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash || !parsed.hostname) invalid();
  return selected;
}
function jsonValue(value: unknown, depth = 0): unknown {
  if (depth > 8) invalid();
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return text(value);
  if (typeof value === "number") { if (!Number.isFinite(value)) invalid(); return value; }
  if (Array.isArray(value)) { if (value.length > 1000) invalid(); return value.map((item) => jsonValue(item, depth + 1)); }
  const raw = object(value);
  if (Object.keys(raw).length > 1000) invalid();
  return Object.fromEntries(Object.entries(raw).map(([key, item]) => [text(key, 512), jsonValue(item, depth + 1)]));
}

export function parseHrPanoramaSource(value: unknown): HrPanoramaSource {
  const raw = object(value); exact(raw, SOURCE_KEYS);
  if (raw.source_kind !== "company" || typeof raw.active !== "boolean") invalid();
  const aliases = stringList(raw.aliases, 20);
  const approvedUrls = stringList(raw.approved_urls, 20).map(httpsUrl);
  if (approvedUrls.length < 1) invalid();
  return { sourceId: id(raw.source_id), sourceKind: "company", canonicalName: text(raw.canonical_name, 500), aliases, approvedUrls, active: raw.active, createdAt: timestamp(raw.created_at), updatedAt: timestamp(raw.updated_at) };
}

export function parseHrPanoramaRun(value: unknown): HrPanoramaRun {
  const raw = object(value); exact(raw, RUN_KEYS);
  if (!RUN_STATES.has(String(raw.state)) || (raw.error_code !== null && (typeof raw.error_code !== "string" || !ERROR_CODE.test(raw.error_code)))) invalid();
  const selectedSourceIds = idList(raw.selected_source_ids, 100);
  if (selectedSourceIds.length < 1) invalid();
  const failures = object(raw.source_failures);
  const allowed = new Set(selectedSourceIds);
  if (Object.entries(failures).some(([sourceId, code]) => !allowed.has(sourceId) || typeof code !== "string" || !ERROR_CODE.test(code))) invalid();
  const state = raw.state as HrPanoramaRun["state"];
  const errorCode = raw.error_code as string | null;
  const startedAt = nullableTimestamp(raw.started_at);
  const finishedAt = nullableTimestamp(raw.finished_at);
  const failureCount = Object.keys(failures).length;
  const validLifecycle = (state === "queued" && startedAt === null && finishedAt === null && errorCode === null && failureCount === 0)
    || (state === "running" && startedAt !== null && finishedAt === null && errorCode === null && failureCount === 0)
    || (state === "completed" && startedAt !== null && finishedAt !== null && errorCode === null && failureCount === 0)
    || (state === "partially_completed" && startedAt !== null && finishedAt !== null && errorCode === null && failureCount > 0 && failureCount < selectedSourceIds.length)
    || (state === "failed" && startedAt !== null && finishedAt !== null && errorCode !== null);
  if (!validLifecycle) invalid();
  return { runId: id(raw.run_id), selectedSourceIds, conversationId: id(raw.conversation_id), state, errorCode, sourceFailures: failures as Record<string, string>, rowVersion: positive(raw.row_version), startedAt, finishedAt, createdAt: timestamp(raw.created_at), updatedAt: timestamp(raw.updated_at) };
}

function fact(value: unknown): HrPanoramaFact {
  const raw = object(value); exact(raw, FACT_KEYS);
  return { factId: text(raw.fact_id, 128), text: text(raw.text, 8000), snapshotId: id(raw.snapshot_id), observationId: id(raw.observation_id), sourceUrl: httpsUrl(raw.source_url), observedAt: timestamp(raw.observed_at) };
}
function inference(value: unknown): HrPanoramaInference {
  const raw = object(value); exact(raw, INFERENCE_KEYS);
  const basisFactIds = stringList(raw.basis_fact_ids, 100);
  if (basisFactIds.length < 1) invalid();
  return { text: text(raw.text, 8000), basisFactIds };
}

export function parseHrPanoramaInsight(value: unknown): HrPanoramaInsight {
  const raw = object(value); exact(raw, INSIGHT_KEYS);
  if (!Array.isArray(raw.facts) || !Array.isArray(raw.inferences) || !Array.isArray(raw.unknowns)) invalid();
  const facts = raw.facts.map(fact);
  if (facts.length < 1 || facts.length > 1000) invalid();
  const factIds = new Set(facts.map((item) => item.factId));
  if (factIds.size !== facts.length) invalid();
  const inferences = raw.inferences.map(inference);
  if (inferences.length > 1000 || inferences.some((item) => item.basisFactIds.some((factId) => !factIds.has(factId)))) invalid();
  const unknowns = raw.unknowns.map((item) => { const selected = object(item); exact(selected, UNKNOWN_KEYS); return { text: text(selected.text, 8000) }; });
  if (unknowns.length > 1000) invalid();
  const directionClusters = jsonValue(raw.direction_clusters);
  if (!directionClusters || typeof directionClusters !== "object" || Array.isArray(directionClusters)) invalid();
  if (new TextEncoder().encode(JSON.stringify(directionClusters)).length > 65_536) invalid();
  const selectedSourceIds = idList(raw.selected_source_ids, 100);
  const snapshotIds = idList(raw.snapshot_ids);
  if (selectedSourceIds.length < 1 || snapshotIds.length < 1) invalid();
  return { insightVersionId: id(raw.insight_version_id), runId: id(raw.run_id), versionNumber: positive(raw.version_number), selectedSourceIds, snapshotIds, facts, inferences, unknowns, directionClusters: directionClusters as Record<string, unknown>, summary: text(raw.summary), sourceConversationId: id(raw.source_conversation_id), sourceTurnId: id(raw.source_turn_id), agentId: text(raw.agent_id, 128), modelVersion: text(raw.model_version, 256), createdAt: timestamp(raw.created_at) };
}

function snapshot(value: unknown): HrPanoramaSnapshot {
  const raw = object(value); exact(raw, SNAPSHOT_KEYS);
  if (!JOB_STATES.has(String(raw.status)) || typeof raw.content_sha256 !== "string" || !SHA256.test(raw.content_sha256)) invalid();
  return { snapshotId: id(raw.snapshot_id), runId: id(raw.run_id), sourceId: id(raw.source_id), publicJobKey: text(raw.public_job_key, 512), title: text(raw.title, 1000), location: text(raw.location, 1000), dutyExcerpt: text(raw.duty_excerpt), requirementExcerpt: text(raw.requirement_excerpt), sourceUrl: httpsUrl(raw.source_url), observedAt: timestamp(raw.observed_at), contentSha256: raw.content_sha256, status: raw.status as HrPanoramaSnapshot["status"], createdAt: timestamp(raw.created_at) };
}

function itemList(value: unknown): unknown[] {
  const raw = object(value); exact(raw, new Set(["items"]));
  if (!Array.isArray(raw.items) || raw.items.length > 100) invalid();
  return raw.items;
}

export function parseHrPanoramaReport(value: unknown): HrPanoramaReport {
  const raw = object(value); exact(raw, new Set(["insight", "sources", "snapshots"]));
  if (!Array.isArray(raw.sources) || !Array.isArray(raw.snapshots) || raw.sources.length > 100 || raw.snapshots.length > 1000) invalid();
  const insight = parseHrPanoramaInsight(raw.insight);
  const sources = raw.sources.map(parseHrPanoramaSource);
  const snapshots = raw.snapshots.map(snapshot);
  const sourceIds = new Set(sources.map((item) => item.sourceId));
  const snapshotIds = new Set(snapshots.map((item) => item.snapshotId));
  const snapshotById = new Map(snapshots.map((item) => [item.snapshotId, item]));
  if (sourceIds.size !== sources.length || sourceIds.size !== insight.selectedSourceIds.length || insight.selectedSourceIds.some((sourceId) => !sourceIds.has(sourceId)) || snapshots.some((item) => item.runId !== insight.runId || !sourceIds.has(item.sourceId)) || snapshotIds.size !== snapshots.length || snapshotIds.size !== insight.snapshotIds.length || insight.snapshotIds.some((snapshotId) => !snapshotIds.has(snapshotId)) || insight.facts.some((item) => { const evidence = snapshotById.get(item.snapshotId); return !evidence || evidence.sourceUrl !== item.sourceUrl || evidence.observedAt !== item.observedAt; })) invalid();
  return { insight, sources, snapshots };
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(platformPath(path), { cache: "no-store", credentials: "same-origin", ...init, headers: { Accept: "application/json", ...init.headers } });
  if (!response.ok) throw new HrPanoramaApiError(response.status);
  return response.json();
}

function mutation(csrfToken: string, requestId: string, body: unknown, signal?: AbortSignal): RequestInit {
  inputId(requestId);
  return { method: "POST", cache: "no-store", signal, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, "Idempotency-Key": requestId }, body: JSON.stringify(body) };
}

function sameIds(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value) => right.includes(value));
}

export function createHrPanoramaApi(csrfToken: string) {
  return {
    listCompanies(signal?: AbortSignal): Promise<HrPanoramaSource[]> { return request("/api/hr/panorama/sources?limit=100", { signal }).then((value) => itemList(value).map(parseHrPanoramaSource)); },
    addCompany(input: AddHrPanoramaCompanyInput, requestId: string, signal?: AbortSignal): Promise<HrPanoramaSource> { return request("/api/hr/panorama/sources", mutation(csrfToken, requestId, { canonical_name: input.canonicalName, aliases: input.aliases, approved_urls: input.approvedUrls }, signal)).then(parseHrPanoramaSource); },
    startRun(input: StartHrPanoramaRunInput, requestId: string, signal?: AbortSignal): Promise<HrPanoramaRun> {
      const sourceIds = input.sourceIds.map(inputId);
      const conversationId = input.conversationId ? inputId(input.conversationId) : undefined;
      return request("/api/hr/panorama/runs", mutation(csrfToken, requestId, { source_ids: sourceIds, ...(conversationId ? { conversation_id: conversationId } : {}) }, signal)).then((value) => {
        const parsed = parseHrPanoramaRun(value);
        if (!sameIds(parsed.selectedSourceIds, sourceIds) || (conversationId && parsed.conversationId !== conversationId)) invalid();
        return parsed;
      });
    },
    runStatus(runId: string, signal?: AbortSignal): Promise<HrPanoramaRun> {
      const selected = inputId(runId);
      return request(`/api/hr/panorama/runs/${encodeURIComponent(selected)}`, { signal }).then((value) => {
        const parsed = parseHrPanoramaRun(value);
        if (parsed.runId !== selected) invalid();
        return parsed;
      });
    },
    listReports(signal?: AbortSignal): Promise<HrPanoramaInsight[]> { return request("/api/hr/panorama/reports?limit=100", { signal }).then((value) => itemList(value).map(parseHrPanoramaInsight)); },
    report(insightVersionId: string, signal?: AbortSignal): Promise<HrPanoramaReport> {
      const selected = inputId(insightVersionId);
      return request(`/api/hr/panorama/reports/${encodeURIComponent(selected)}`, { signal }).then((value) => {
        const parsed = parseHrPanoramaReport(value);
        if (parsed.insight.insightVersionId !== selected) invalid();
        return parsed;
      });
    },
  };
}

export type HrPanoramaApi = ReturnType<typeof createHrPanoramaApi>;
