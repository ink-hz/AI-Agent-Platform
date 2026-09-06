import { platformPath } from "./auth";
import type {
  HrPanoramaEvidence, HrPanoramaFact, HrPanoramaInference, HrPanoramaInsight, HrPanoramaPublication,
  HrPanoramaReport, HrPanoramaReportSummary, HrPanoramaSnapshot, HrPanoramaSource,
  HrPanoramaSourceCoverage,
} from "./hrPanoramaTypes";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;
const ERROR_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const JOB_STATES = new Set(["open", "closed", "unknown"]);
const SOURCE_KEYS = new Set(["source_id", "source_kind", "canonical_name", "aliases", "approved_urls", "active", "created_at", "updated_at"]);
const FACT_KEYS = new Set(["fact_id", "text", "snapshot_id", "observation_id", "source_url", "observed_at"]);
const INFERENCE_KEYS = new Set(["text", "basis_fact_ids"]);
const UNKNOWN_KEYS = new Set(["text"]);
const INSIGHT_KEYS = new Set(["insight_version_id", "run_id", "production_batch_id", "version_number", "selected_source_ids", "snapshot_ids", "facts", "inferences", "unknowns", "direction_clusters", "summary", "source_conversation_id", "source_turn_id", "agent_id", "model_version", "created_at"]);
const SNAPSHOT_KEYS = new Set(["snapshot_id", "run_id", "production_batch_id", "observation_id", "source_id", "public_job_key", "title", "location", "duty_excerpt", "requirement_excerpt", "source_url", "observed_at", "content_sha256", "status", "created_at"]);
const PUBLICATION_KEYS = new Set(["publication_id", "bundle_id", "batch_id", "insight_version_id", "manifest_sha256", "coverage_state", "source_coverage", "generated_at", "published_at"]);
const COVERAGE_KEYS = new Set(["source_id", "state", "observed_at", "source_urls", "job_count", "error_code", "channel_failures"]);
const EVIDENCE_KEYS = new Set(["source_id", "source_url", "attempt_number", "state", "error_code", "sha256", "mime", "size_bytes", "normalized_job_count", "observed_at"]);
const COVERAGE_STATES = new Set(["succeeded", "empty_confirmed", "partial", "failed", "not_observed"]);
const SUMMARY_INSIGHT_KEYS = new Set(["insight_version_id", "version_number", "selected_source_ids", "summary", "created_at"]);

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
function exact(value: Record<string, unknown>, keys: ReadonlySet<string>, optional = new Set<string>()): void {
  const actual = Object.keys(value);
  if (actual.some((key) => !keys.has(key)) || [...keys].some((key) => !optional.has(key) && !(key in value))) invalid();
}
function id(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) invalid(); return value; }
function nullableId(value: unknown): string | null { return value === null ? null : id(value); }
function inputId(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) throw new Error("HR Panorama identifier invalid"); return value; }
function text(value: unknown, maximum = 32768): string { if (typeof value !== "string" || !value.trim() || value.includes("\0") || value.length > maximum) invalid(); return value; }
function timestamp(value: unknown): string { const selected = text(value, 64); if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(selected) || !Number.isFinite(Date.parse(selected))) invalid(); return selected; }
function positive(value: unknown): number { if (!Number.isSafeInteger(value) || Number(value) < 1) invalid(); return Number(value); }
function nonnegative(value: unknown): number { if (!Number.isSafeInteger(value) || Number(value) < 0) invalid(); return Number(value); }
function stringList(value: unknown, maximum = 1000): string[] { if (!Array.isArray(value) || value.length > maximum) invalid(); const values = value.map((item) => text(item, 2048)); if (new Set(values).size !== values.length) invalid(); return values; }
function idList(value: unknown, maximum = 1000): string[] { if (!Array.isArray(value) || value.length > maximum) invalid(); const values = value.map(id); if (new Set(values).size !== values.length) invalid(); return values; }
function httpsUrl(value: unknown): string { const selected = text(value, 2048); let parsed: URL; try { parsed = new URL(selected); } catch { invalid(); } if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash || !parsed.hostname) invalid(); return selected; }
function jsonValue(value: unknown, depth = 0): unknown {
  if (depth > 8) invalid();
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return text(value);
  if (typeof value === "number") { if (!Number.isFinite(value)) invalid(); return value; }
  if (Array.isArray(value)) { if (value.length > 1000) invalid(); return value.map((item) => jsonValue(item, depth + 1)); }
  const raw = object(value); if (Object.keys(raw).length > 1000) invalid();
  return Object.fromEntries(Object.entries(raw).map(([key, item]) => [text(key, 512), jsonValue(item, depth + 1)]));
}

export function parseHrPanoramaSource(value: unknown): HrPanoramaSource {
  const raw = object(value); exact(raw, SOURCE_KEYS);
  if (raw.source_kind !== "company" || typeof raw.active !== "boolean") invalid();
  const approvedUrls = stringList(raw.approved_urls, 20).map(httpsUrl); if (!approvedUrls.length) invalid();
  return { sourceId: id(raw.source_id), sourceKind: "company", canonicalName: text(raw.canonical_name, 500), aliases: stringList(raw.aliases, 20), approvedUrls, active: raw.active, createdAt: timestamp(raw.created_at), updatedAt: timestamp(raw.updated_at) };
}

function fact(value: unknown): HrPanoramaFact { const raw = object(value); exact(raw, FACT_KEYS); return { factId: text(raw.fact_id, 128), text: text(raw.text, 8000), snapshotId: id(raw.snapshot_id), observationId: id(raw.observation_id), sourceUrl: httpsUrl(raw.source_url), observedAt: timestamp(raw.observed_at) }; }
function inference(value: unknown): HrPanoramaInference { const raw = object(value); exact(raw, INFERENCE_KEYS); const basisFactIds = stringList(raw.basis_fact_ids, 100); if (!basisFactIds.length) invalid(); return { text: text(raw.text, 8000), basisFactIds }; }

export function parseHrPanoramaInsight(value: unknown): HrPanoramaInsight {
  const raw = object(value); exact(raw, INSIGHT_KEYS);
  if (!Array.isArray(raw.facts) || !Array.isArray(raw.inferences) || !Array.isArray(raw.unknowns)) invalid();
  const facts = raw.facts.map(fact); if (facts.length > 1000 || new Set(facts.map((item) => item.factId)).size !== facts.length) invalid();
  const factIds = new Set(facts.map((item) => item.factId));
  const inferences = raw.inferences.map(inference); if (inferences.length > 1000 || inferences.some((item) => item.basisFactIds.some((factId) => !factIds.has(factId)))) invalid();
  const unknowns = raw.unknowns.map((item) => { const selected = object(item); exact(selected, UNKNOWN_KEYS); return { text: text(selected.text, 8000) }; });
  const directionClusters = jsonValue(raw.direction_clusters); if (!directionClusters || typeof directionClusters !== "object" || Array.isArray(directionClusters)) invalid();
  const selectedSourceIds = idList(raw.selected_source_ids, 100); const snapshotIds = idList(raw.snapshot_ids, 10000); if (!selectedSourceIds.length) invalid();
  const runId = nullableId(raw.run_id); const productionBatchId = nullableId(raw.production_batch_id); if ((runId === null) === (productionBatchId === null)) invalid();
  return { insightVersionId: id(raw.insight_version_id), runId, productionBatchId, versionNumber: positive(raw.version_number), selectedSourceIds, snapshotIds, facts, inferences, unknowns, directionClusters: directionClusters as Record<string, unknown>, summary: text(raw.summary), sourceConversationId: nullableId(raw.source_conversation_id), sourceTurnId: nullableId(raw.source_turn_id), agentId: text(raw.agent_id, 128), modelVersion: text(raw.model_version, 256), createdAt: timestamp(raw.created_at) };
}

function snapshot(value: unknown): HrPanoramaSnapshot {
  const raw = object(value); exact(raw, SNAPSHOT_KEYS); if (!JOB_STATES.has(String(raw.status)) || typeof raw.content_sha256 !== "string" || !SHA256.test(raw.content_sha256)) invalid();
  const runId = nullableId(raw.run_id); const productionBatchId = nullableId(raw.production_batch_id); if ((runId === null) === (productionBatchId === null)) invalid();
  return { snapshotId: id(raw.snapshot_id), runId, productionBatchId, observationId: nullableId(raw.observation_id), sourceId: id(raw.source_id), publicJobKey: text(raw.public_job_key, 512), title: text(raw.title, 1000), location: text(raw.location, 1000), dutyExcerpt: text(raw.duty_excerpt), requirementExcerpt: text(raw.requirement_excerpt), sourceUrl: httpsUrl(raw.source_url), observedAt: timestamp(raw.observed_at), contentSha256: raw.content_sha256, status: raw.status as HrPanoramaSnapshot["status"], createdAt: timestamp(raw.created_at) };
}

function coverage(value: unknown): HrPanoramaSourceCoverage {
  const raw = object(value); exact(raw, COVERAGE_KEYS, new Set(["error_code", "channel_failures"]));
  if (!COVERAGE_STATES.has(String(raw.state))) invalid();
  const sourceUrls = stringList(raw.source_urls, 20).map(httpsUrl); if (!sourceUrls.length) invalid();
  const channelFailures = raw.channel_failures === undefined ? undefined : object(raw.channel_failures);
  if (channelFailures && Object.entries(channelFailures).some(([url, reason]) => !sourceUrls.includes(url) || typeof reason !== "string" || !ERROR_CODE.test(reason))) invalid();
  const errorCode = raw.error_code === undefined ? undefined : text(raw.error_code, 64); if (errorCode && !ERROR_CODE.test(errorCode)) invalid();
  if (raw.state === "failed" && !errorCode) invalid();
  return { sourceId: id(raw.source_id), state: raw.state as HrPanoramaSourceCoverage["state"], observedAt: timestamp(raw.observed_at), sourceUrls, jobCount: nonnegative(raw.job_count), ...(errorCode ? { errorCode } : {}), ...(channelFailures ? { channelFailures: channelFailures as Record<string, string> } : {}) };
}

function publication(value: unknown): HrPanoramaPublication {
  const raw = object(value); exact(raw, PUBLICATION_KEYS); if (!COVERAGE_STATES.has(String(raw.coverage_state))) invalid(); if (!Array.isArray(raw.source_coverage) || !raw.source_coverage.length || raw.source_coverage.length > 100) invalid();
  const sourceCoverage = raw.source_coverage.map(coverage); if (new Set(sourceCoverage.map((item) => item.sourceId)).size !== sourceCoverage.length) invalid();
  const publicationId = id(raw.publication_id); const bundleId = id(raw.bundle_id); if (publicationId !== bundleId || typeof raw.manifest_sha256 !== "string" || !SHA256.test(raw.manifest_sha256)) invalid();
  return { publicationId, bundleId, batchId: id(raw.batch_id), insightVersionId: id(raw.insight_version_id), manifestSha256: raw.manifest_sha256, coverageState: raw.coverage_state as HrPanoramaPublication["coverageState"], sourceCoverage, generatedAt: timestamp(raw.generated_at), publishedAt: timestamp(raw.published_at) };
}

function evidence(value: unknown): HrPanoramaEvidence {
  const raw = object(value); exact(raw, EVIDENCE_KEYS); if (raw.state !== "succeeded" && raw.state !== "failed") invalid();
  if (typeof raw.sha256 !== "string" || !SHA256.test(raw.sha256)) invalid();
  const errorCode = raw.error_code === null ? null : text(raw.error_code, 64); if (errorCode && !ERROR_CODE.test(errorCode)) invalid();
  return { sourceId: id(raw.source_id), sourceUrl: httpsUrl(raw.source_url), attemptNumber: positive(raw.attempt_number), state: raw.state, errorCode, sha256: raw.sha256, mime: text(raw.mime, 255), sizeBytes: nonnegative(raw.size_bytes), normalizedJobCount: nonnegative(raw.normalized_job_count), observedAt: timestamp(raw.observed_at) };
}

function insightSummary(value: unknown): HrPanoramaReportSummary["insight"] {
  const raw = object(value);
  if (Object.keys(raw).length === SUMMARY_INSIGHT_KEYS.size && Object.keys(raw).every((key) => SUMMARY_INSIGHT_KEYS.has(key))) {
    return { insightVersionId: id(raw.insight_version_id), versionNumber: positive(raw.version_number), selectedSourceIds: idList(raw.selected_source_ids, 100), summary: text(raw.summary), createdAt: timestamp(raw.created_at) };
  }
  return parseHrPanoramaInsight(raw);
}
function summary(value: unknown): HrPanoramaReportSummary { const raw = object(value); exact(raw, new Set(["publication", "insight"])); const selectedPublication = publication(raw.publication); const insight = insightSummary(raw.insight); if (selectedPublication.insightVersionId !== insight.insightVersionId) invalid(); return { publication: selectedPublication, insight }; }

export function parseHrPanoramaReport(value: unknown): HrPanoramaReport {
  const raw = object(value); exact(raw, new Set(["publication", "insight", "sources", "snapshots", "evidence", "analysis_usage"])); if (!Array.isArray(raw.sources) || !Array.isArray(raw.snapshots) || !Array.isArray(raw.evidence) || !Array.isArray(raw.analysis_usage) || raw.sources.length > 100 || raw.snapshots.length > 10000 || raw.evidence.length > 300 || raw.analysis_usage.length > 1000) invalid();
  const selectedPublication = publication(raw.publication); const selectedInsight = parseHrPanoramaInsight(raw.insight); if (selectedPublication.insightVersionId !== selectedInsight.insightVersionId) invalid(); const sources = raw.sources.map(parseHrPanoramaSource); const snapshots = raw.snapshots.map(snapshot); const evidenceRecords = raw.evidence.map(evidence);
  const sourceIds = new Set(sources.map((item) => item.sourceId)); const snapshotIds = new Set(snapshots.map((item) => item.snapshotId)); const snapshotById = new Map(snapshots.map((item) => [item.snapshotId, item]));
  if (sourceIds.size !== sources.length || sourceIds.size !== selectedInsight.selectedSourceIds.length || selectedInsight.selectedSourceIds.some((sourceId) => !sourceIds.has(sourceId)) || snapshots.some((item) => item.productionBatchId !== selectedPublication.batchId || !sourceIds.has(item.sourceId)) || snapshotIds.size !== snapshots.length || snapshotIds.size !== selectedInsight.snapshotIds.length || selectedInsight.snapshotIds.some((snapshotId) => !snapshotIds.has(snapshotId)) || selectedInsight.facts.some((item) => { const evidence = snapshotById.get(item.snapshotId); return !evidence || evidence.sourceUrl !== item.sourceUrl || evidence.observedAt !== item.observedAt || evidence.observationId !== item.observationId; })) invalid();
  if (evidenceRecords.some((item) => !sourceIds.has(item.sourceId))) invalid();
  const analysisUsage = raw.analysis_usage.map((item) => jsonValue(item) as Record<string, unknown>);
  return { publication: selectedPublication, insight: selectedInsight, sources, snapshots, evidence: evidenceRecords, analysisUsage };
}

async function request(path: string, signal?: AbortSignal): Promise<Response> { return fetch(platformPath(path), { cache: "no-store", credentials: "same-origin", signal, headers: { Accept: "application/json" } }); }
async function json(response: Response): Promise<unknown> { if (!response.ok) throw new HrPanoramaApiError(response.status); return response.json(); }

export function createHrPanoramaApi(_csrfToken: string) {
  return {
    async currentReport(signal?: AbortSignal): Promise<HrPanoramaReport | null> { const response = await request("/api/hr/panorama/current", signal); if (response.status === 204) return null; return parseHrPanoramaReport(await json(response)); },
    async listReports(signal?: AbortSignal): Promise<HrPanoramaReportSummary[]> { const raw = object(await json(await request("/api/hr/panorama/reports?limit=100", signal))); exact(raw, new Set(["items"])); if (!Array.isArray(raw.items) || raw.items.length > 100) invalid(); return raw.items.map(summary); },
    async report(publicationId: string, signal?: AbortSignal): Promise<HrPanoramaReport> { const selected = inputId(publicationId); const parsed = parseHrPanoramaReport(await json(await request(`/api/hr/panorama/reports/${encodeURIComponent(selected)}`, signal))); if (parsed.publication.publicationId !== selected) invalid(); return parsed; },
  };
}

export type HrPanoramaApi = ReturnType<typeof createHrPanoramaApi>;
