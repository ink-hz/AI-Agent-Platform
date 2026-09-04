import { platformPath } from "./auth";
import type {
  HrCandidate, HrCandidateAnalysisKind, HrCandidateAnalysisVersion, HrCandidateDocument,
  HrCandidateDraft, HrConfirmedCandidate, HrContextComparison, HrContextVersion,
  HrDownloadTicket, HrHumanFeedback, HrPositionCandidate, HrPositionResources,
  HrCandidateTaskKind, HrPositionTaskKind, HrStartableTaskKind, HrTaskKind, HrTaskRecord,
} from "./hrR12Types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;
const DRAFT_STATES = new Set(["pending", "processing", "ready", "failed", "confirmed", "dismissed"]);
const CONTEXT_STATES = new Set(["draft", "confirmed", "superseded"]);
const ANALYSIS_KINDS = new Set(["resume_extract", "match", "candidate_interview_plan", "comparison"]);
const TASK_KINDS = new Set(["jd", "jr", "talent_profile", "sourcing_strategy", "position_interview_plan", "candidate_match", "candidate_interview_plan", "candidate_comparison"]);

export class HrR12ApiError extends Error {
  constructor(public readonly status: number, public readonly detail: unknown = null) {
    super(`HR R1.2 API ${status}`); this.name = "HrR12ApiError";
  }
}

function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function identifier(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) throw new Error("HR R1.2 identifier invalid"); return value; }
function string(value: unknown, message = "HR R1.2 response invalid"): string { if (typeof value !== "string" || value.length === 0) throw new Error(message); return value; }
function nullableString(value: unknown): string | null { return value === null ? null : string(value); }
function positive(value: unknown): number { if (!Number.isSafeInteger(value) || Number(value) < 1) throw new Error("HR R1.2 response invalid"); return Number(value); }
function object(value: unknown): Record<string, unknown> { if (!record(value)) throw new Error("HR R1.2 response invalid"); return value; }
function stringList(value: unknown): string[] { if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) throw new Error("HR R1.2 response invalid"); return [...value]; }
function idList(value: unknown): string[] { return stringList(value).map(identifier); }
function items(value: unknown): unknown[] { if (!record(value) || !Array.isArray(value.items)) throw new Error("HR R1.2 list response invalid"); return value.items; }

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(platformPath(path), { credentials: "same-origin", headers: { Accept: "application/json", ...init.headers }, ...init });
  if (!response.ok) { let detail: unknown = null; try { detail = await response.json(); } catch { /* opaque */ } throw new HrR12ApiError(response.status, detail); }
  return response.json();
}
function mutation(csrfToken: string, requestId: string, body: unknown, signal?: AbortSignal): RequestInit {
  identifier(requestId);
  return { method: "POST", signal, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, "Idempotency-Key": requestId }, body: JSON.stringify(body) };
}

function resources(value: unknown): HrPositionResources {
  if (!record(value) || !Array.isArray(value.materials) || !Array.isArray(value.artifacts)) throw new Error("HR R1.2 resource response invalid");
  const item = (rawValue: unknown, artifact = false) => {
    const raw = object(rawValue);
    if (typeof raw.filename !== "string" || typeof raw.media_type !== "string" || typeof raw.state !== "string" || !Number.isSafeInteger(raw.size_bytes) || typeof raw.created_at !== "string" || typeof raw.preview_available !== "boolean" || typeof raw.download_available !== "boolean") throw new Error("HR R1.2 resource response invalid");
    const base = { attachmentId: identifier(raw.attachment_id), filename: raw.filename, mediaType: raw.media_type, state: raw.state, sizeBytes: Number(raw.size_bytes), createdAt: raw.created_at, sourceConversationId: raw.source_conversation_id === null ? null : identifier(raw.source_conversation_id), sourceTurnId: raw.source_turn_id === null ? null : identifier(raw.source_turn_id), previewAvailable: raw.preview_available, downloadAvailable: raw.download_available };
    return artifact ? { ...base, artifactId: identifier(raw.artifact_id), artifactVersion: positive(raw.artifact_version) } : base;
  };
  return { materials: value.materials.map((raw) => item(raw)), artifacts: value.artifacts.map((raw) => item(raw, true)) } as HrPositionResources;
}
function ticket(value: unknown): HrDownloadTicket {
  const raw = object(value);
  if (typeof raw.expires_at !== "string" || typeof raw.content_path !== "string" || !/^\/api\/v1\/attachments\/content\/[A-Za-z0-9_-]{32,256}$/.test(raw.content_path)) throw new Error("HR R1.2 ticket response invalid");
  return { contentPath: raw.content_path, expiresAt: raw.expires_at };
}
function contextVersion(value: unknown): HrContextVersion {
  const raw = object(value);
  if (!CONTEXT_STATES.has(String(raw.state)) || !record(raw.modules)) throw new Error("HR R1.2 context response invalid");
  const modules: Record<string, Record<string, unknown>> = {};
  for (const [key, module] of Object.entries(raw.modules)) modules[key] = object(module);
  return { contextVersionId: identifier(raw.context_version_id), positionId: identifier(raw.position_id), displayVersion: positive(raw.version_number), status: raw.state as HrContextVersion["status"], modules, summary: string(raw.summary), officialVersionId: raw.official_version_id === null ? null : identifier(raw.official_version_id), baseContextVersionId: raw.base_context_version_id === null ? null : identifier(raw.base_context_version_id), sourceConversationId: raw.source_conversation_id === null ? null : identifier(raw.source_conversation_id), sourceTurnId: raw.source_turn_id === null ? null : identifier(raw.source_turn_id), sourceArtifactVersionId: raw.source_artifact_version_id === null ? null : identifier(raw.source_artifact_version_id), sourceMaterialAttachmentIds: idList(raw.source_material_attachment_ids), agentId: nullableString(raw.agent_id), modelVersion: nullableString(raw.model_version), rowVersion: positive(raw.row_version), createdAt: string(raw.created_at), confirmedAt: raw.confirmed_at === null ? null : string(raw.confirmed_at) };
}
function draft(value: unknown): HrCandidateDraft {
  const raw = object(value);
  if (!DRAFT_STATES.has(String(raw.state)) || (raw.error_code !== null && typeof raw.error_code !== "string")) throw new Error("HR R1.2 candidate response invalid");
  return { draftId: identifier(raw.draft_id), positionId: identifier(raw.position_id), attachmentId: identifier(raw.attachment_id), batchRequestId: identifier(raw.batch_request_id), state: raw.state as HrCandidateDraft["state"], extractedFacts: object(raw.extracted_facts), identityCandidateIds: idList(raw.identity_candidates), errorCode: raw.error_code as string | null, rowVersion: positive(raw.row_version), createdAt: string(raw.created_at), updatedAt: string(raw.updated_at) };
}
function candidate(value: unknown): HrCandidate { const raw = object(value); return { candidateId: identifier(raw.candidate_id), stableName: string(raw.stable_name), facts: object(raw.facts), createdAt: string(raw.created_at), updatedAt: string(raw.updated_at) }; }
function document(value: unknown): HrCandidateDocument {
  const raw = object(value);
  if ((raw.status !== "active" && raw.status !== "erased") || typeof raw.content_sha256 !== "string" || !SHA256.test(raw.content_sha256)) throw new Error("HR R1.2 candidate response invalid");
  return { documentId: identifier(raw.document_id), candidateId: identifier(raw.candidate_id), attachmentId: identifier(raw.attachment_id), sourceDraftId: identifier(raw.source_draft_id), documentKind: string(raw.document_kind), versionNumber: positive(raw.version_number), contentSha256: raw.content_sha256, status: raw.status, createdAt: string(raw.created_at) };
}
function positionCandidate(value: unknown): HrPositionCandidate {
  const raw = object(value); if (raw.status !== "active" && raw.status !== "archived") throw new Error("HR R1.2 candidate response invalid");
  return { positionCandidateId: identifier(raw.position_candidate_id), positionId: identifier(raw.position_id), candidateId: identifier(raw.candidate_id), contextVersionId: identifier(raw.context_version_id), sourceDraftId: identifier(raw.source_draft_id), status: raw.status, rowVersion: positive(raw.row_version), createdAt: string(raw.created_at), updatedAt: string(raw.updated_at) };
}
function confirmedCandidate(value: unknown): HrConfirmedCandidate { const raw = object(value); return { candidate: candidate(raw.candidate), document: document(raw.document), positionCandidate: positionCandidate(raw.position_candidate) }; }
function analysis(value: unknown): HrCandidateAnalysisVersion {
  const raw = object(value); if (!ANALYSIS_KINDS.has(String(raw.analysis_kind)) || !Array.isArray(raw.evidence) || !raw.evidence.every(record)) throw new Error("HR R1.2 analysis response invalid");
  return { analysisVersionId: identifier(raw.analysis_version_id), positionCandidateId: identifier(raw.position_candidate_id), positionId: identifier(raw.position_id), candidateId: identifier(raw.candidate_id), contextVersionId: identifier(raw.context_version_id), versionNumber: positive(raw.version_number), analysisKind: raw.analysis_kind as HrCandidateAnalysisKind, documentIds: idList(raw.document_ids), feedbackIds: idList(raw.feedback_ids), result: object(raw.result), evidence: raw.evidence.map((entry) => ({ ...entry })), unknowns: stringList(raw.unknowns), conflicts: stringList(raw.conflicts), verificationQuestions: stringList(raw.verification_questions), agentVersion: string(raw.agent_version), modelVersion: string(raw.model_version), createdAt: string(raw.created_at) };
}
function feedback(value: unknown): HrHumanFeedback {
  const raw = object(value); if (!["accepted", "rejected", "correction"].includes(String(raw.feedback_kind))) throw new Error("HR R1.2 feedback response invalid");
  return { feedbackId: identifier(raw.feedback_id), positionCandidateId: identifier(raw.position_candidate_id), analysisVersionId: identifier(raw.analysis_version_id), feedbackKind: raw.feedback_kind as HrHumanFeedback["feedbackKind"], conclusionKey: string(raw.conclusion_key), correction: raw.correction === null ? null : string(raw.correction), reason: string(raw.reason), createdAt: string(raw.created_at) };
}
function task(value: unknown, requireCandidateBinding = false): HrTaskRecord {
  const raw = object(value);
  if (!["accepted", "running", "completed", "failed"].includes(String(raw.status)) || !TASK_KINDS.has(String(raw.task_kind))) throw new Error("HR R1.2 task response invalid");
  const positionCandidateId = raw.position_candidate_id == null ? null : identifier(raw.position_candidate_id);
  const candidateId = raw.candidate_id == null ? null : identifier(raw.candidate_id);
  const conversationId = raw.conversation_id == null ? null : identifier(raw.conversation_id);
  const turnId = raw.turn_id == null ? null : identifier(raw.turn_id);
  if ((conversationId === null) !== (turnId === null)) throw new Error("HR R1.2 task binding invalid");
  if (requireCandidateBinding && (!positionCandidateId || !candidateId)) throw new Error("HR R1.2 task binding invalid");
  return {
    taskId: string(raw.task_id),
    status: raw.status as HrTaskRecord["status"],
    taskKind: raw.task_kind as HrTaskKind,
    error: raw.error === undefined || raw.error === null ? null : string(raw.error),
    conversationId, turnId, positionCandidateId, candidateId,
  };
}

export interface ConfirmCandidateInput { expectedRowVersion: number; contextVersionId: string; stableName: string; confirmedFacts: Record<string, unknown>; mergeCandidateId: string | null; }
export interface CandidateAnalysisInput { contextVersionId: string; documentIds: string[]; analysisKind: Exclude<HrCandidateAnalysisKind, "comparison">; result: Record<string, unknown>; evidence: Record<string, unknown>[]; unknowns: string[]; conflicts: string[]; verificationQuestions: string[]; agentVersion: string; modelVersion: string; }
export interface HumanFeedbackInput { analysisVersionId: string; feedbackKind: HrHumanFeedback["feedbackKind"]; conclusionKey: string; correction: string | null; reason: string; }
interface BaseTaskInput { materialIds?: string[]; conversationId?: string; }
export interface PositionTaskInput extends BaseTaskInput {
  contextVersionId?: string; candidate?: never;
}
export interface CandidateTaskInput extends BaseTaskInput {
  contextVersionId: string;
  candidate: { candidateId: string; positionCandidateId: string };
}
export type StartTaskInput<K extends HrStartableTaskKind> = K extends HrCandidateTaskKind
  ? CandidateTaskInput : PositionTaskInput;

function taskBody<K extends HrStartableTaskKind>(taskKind: K, input: StartTaskInput<K>) {
  const candidateKind = taskKind === "candidate_match" || taskKind === "candidate_interview_plan";
  const candidate = "candidate" in input ? input.candidate : undefined;
  if (candidateKind && (!candidate || !input.contextVersionId)) throw new Error("candidate task envelope invalid");
  if (!candidateKind && candidate) throw new Error("position task envelope invalid");
  return {
    task_kind: taskKind,
    context_version_id: input.contextVersionId ? identifier(input.contextVersionId) : null,
    candidate_id: candidate ? identifier(candidate.candidateId) : null,
    position_candidate_id: candidate ? identifier(candidate.positionCandidateId) : null,
    material_ids: (input.materialIds ?? []).map(identifier),
    conversation_id: input.conversationId ? identifier(input.conversationId) : null,
  };
}

function startableTaskKind(value: HrTaskKind): asserts value is HrStartableTaskKind {
  if (value === "candidate_comparison") throw new Error("task kind invalid");
}

export function createHrR12Api(csrfToken: string) {
  const positionPath = (positionId: string, suffix = "") => `/api/hr/positions/${encodeURIComponent(identifier(positionId))}${suffix}`;
  const write = (path: string, requestId: string, body: unknown, signal?: AbortSignal) => request(path, mutation(csrfToken, requestId, body, signal));
  return {
    resources(positionId: string, signal?: AbortSignal): Promise<HrPositionResources> { return request(positionPath(positionId, "/resources"), { signal }).then(resources); },
    downloadResource(positionId: string, attachmentId: string, requestId: string, purpose: "preview" | "download" = "download", signal?: AbortSignal): Promise<HrDownloadTicket> { return write(positionPath(positionId, `/resources/${encodeURIComponent(identifier(attachmentId))}/ticket`), requestId, { purpose }, signal).then(ticket); },
    async context(positionId: string, signal?: AbortSignal): Promise<{ current: HrContextVersion | null; drafts: HrContextVersion[]; history: HrContextVersion[] }> {
      const [summary, history] = await Promise.all([request(positionPath(positionId, "/context"), { signal }), request(positionPath(positionId, "/context/versions"), { signal })]);
      if (!record(summary) || (summary.current !== null && !record(summary.current)) || !Array.isArray(summary.drafts)) throw new Error("HR R1.2 context response invalid");
      return { current: summary.current === null ? null : contextVersion(summary.current), drafts: summary.drafts.map(contextVersion), history: items(history).map(contextVersion) };
    },
    compareContext(positionId: string, leftId: string, rightId: string, signal?: AbortSignal): Promise<HrContextComparison> { const query = new URLSearchParams({ left: identifier(leftId), right: identifier(rightId) }); return request(positionPath(positionId, `/context/compare?${query}`), { signal }).then((value) => { const raw = object(value); return { leftVersionId: identifier(raw.left_version_id), rightVersionId: identifier(raw.right_version_id), changedModules: stringList(raw.changed_modules), left: object(raw.left), right: object(raw.right) }; }); },
    confirmContext(positionId: string, contextVersionId: string, currentContextVersionId: string | null, moduleNames: string[], rowVersion: number, requestId: string, signal?: AbortSignal): Promise<HrContextVersion> { return write(positionPath(positionId, `/context/drafts/${encodeURIComponent(identifier(contextVersionId))}/confirm`), requestId, { expected_current_context_version_id: currentContextVersionId === null ? null : identifier(currentContextVersionId), expected_draft_row_version: rowVersion, module_names: moduleNames }, signal).then(contextVersion); },
    createCandidateDraftBatch(positionId: string, attachmentIds: string[], requestId: string, signal?: AbortSignal): Promise<{ batchId: string; items: HrCandidateDraft[] }> { return write(positionPath(positionId, "/candidate-drafts:batch"), requestId, { attachment_ids: attachmentIds.map(identifier) }, signal).then((value) => { const raw = object(value); if (!Array.isArray(raw.items)) throw new Error("HR R1.2 candidate response invalid"); return { batchId: identifier(raw.batch_id), items: raw.items.map(draft) }; }); },
    candidateDrafts(positionId: string, signal?: AbortSignal): Promise<HrCandidateDraft[]> { return request(positionPath(positionId, "/candidate-drafts"), { signal }).then((value) => items(value).map(draft)); },
    retryDraft(draftId: string, expectedRowVersion: number, requestId: string, signal?: AbortSignal): Promise<HrCandidateDraft> { return write(`/api/hr/candidate-drafts/${encodeURIComponent(identifier(draftId))}:retry`, requestId, { expected_row_version: expectedRowVersion }, signal).then(draft); },
    confirmDraft(draftId: string, input: ConfirmCandidateInput, requestId: string, signal?: AbortSignal): Promise<HrConfirmedCandidate> { return write(`/api/hr/candidate-drafts/${encodeURIComponent(identifier(draftId))}:confirm`, requestId, { expected_row_version: input.expectedRowVersion, context_version_id: identifier(input.contextVersionId), stable_name: input.stableName, confirmed_facts: input.confirmedFacts, merge_candidate_id: input.mergeCandidateId === null ? null : identifier(input.mergeCandidateId) }, signal).then(confirmedCandidate); },
    positionCandidates(positionId: string, signal?: AbortSignal): Promise<HrPositionCandidate[]> { return request(positionPath(positionId, "/candidates"), { signal }).then((value) => items(value).map(positionCandidate)); },
    candidate(candidateId: string, signal?: AbortSignal): Promise<HrCandidate> { return request(`/api/hr/candidates/${encodeURIComponent(identifier(candidateId))}`, { signal }).then(candidate); },
    candidateDocuments(candidateId: string, signal?: AbortSignal): Promise<HrCandidateDocument[]> { return request(`/api/hr/candidates/${encodeURIComponent(identifier(candidateId))}/documents`, { signal }).then((value) => items(value).map(document)); },
    downloadCandidateDocument(documentId: string, requestId: string, purpose: "preview" | "download" = "download", signal?: AbortSignal): Promise<HrDownloadTicket> { return write(`/api/hr/candidate-documents/${encodeURIComponent(identifier(documentId))}/ticket`, requestId, { purpose }, signal).then(ticket); },
    candidateAnalyses(positionCandidateId: string, signal?: AbortSignal): Promise<HrCandidateAnalysisVersion[]> { return request(`/api/hr/position-candidates/${encodeURIComponent(identifier(positionCandidateId))}/analyses`, { signal }).then((value) => items(value).map(analysis)); },
    createCandidateAnalysis(positionCandidateId: string, input: CandidateAnalysisInput, requestId: string, signal?: AbortSignal): Promise<HrCandidateAnalysisVersion> { return write(`/api/hr/position-candidates/${encodeURIComponent(identifier(positionCandidateId))}/analyses`, requestId, { context_version_id: identifier(input.contextVersionId), document_ids: input.documentIds.map(identifier), analysis_kind: input.analysisKind, result: input.result, evidence: input.evidence, unknowns: input.unknowns, conflicts: input.conflicts, verification_questions: input.verificationQuestions, agent_version: input.agentVersion, model_version: input.modelVersion }, signal).then(analysis); },
    candidateFeedback(positionCandidateId: string, signal?: AbortSignal): Promise<HrHumanFeedback[]> { return request(`/api/hr/position-candidates/${encodeURIComponent(identifier(positionCandidateId))}/feedback`, { signal }).then((value) => items(value).map(feedback)); },
    appendCandidateFeedback(positionCandidateId: string, input: HumanFeedbackInput, requestId: string, signal?: AbortSignal): Promise<HrHumanFeedback> { return write(`/api/hr/position-candidates/${encodeURIComponent(identifier(positionCandidateId))}/feedback`, requestId, { analysis_version_id: identifier(input.analysisVersionId), feedback_kind: input.feedbackKind, conclusion_key: input.conclusionKey, correction: input.correction, reason: input.reason }, signal).then(feedback); },
    compareCandidates(positionId: string, positionCandidateIds: string[], contextVersionId: string, requestId: string, signal?: AbortSignal): Promise<HrCandidateAnalysisVersion> { return write(positionPath(positionId, "/candidate-comparisons"), requestId, { position_candidate_ids: positionCandidateIds.map(identifier), context_version_id: identifier(contextVersionId), agent_version: "hr-r12", model_version: "platform" }, signal).then(analysis); },
    startTask<K extends HrStartableTaskKind>(positionId: string, taskKind: K, requestId: string, input: StartTaskInput<K>, signal?: AbortSignal): Promise<HrTaskRecord> { startableTaskKind(taskKind); return write(positionPath(positionId, "/tasks"), requestId, taskBody(taskKind, input), signal).then(task); },
    activeTasks(positionId: string, signal?: AbortSignal): Promise<HrTaskRecord[]> { return request(positionPath(positionId, "/tasks?status=active"), { signal }).then((value) => items(value).map((item) => task(item))); },
    taskStatus(positionId: string, taskId: string, signal?: AbortSignal): Promise<HrTaskRecord> { return request(positionPath(positionId, `/tasks/${encodeURIComponent(identifier(taskId))}`), { signal }).then((value) => task(value, true)); },
  };
}
export type HrR12Api = ReturnType<typeof createHrR12Api>;
