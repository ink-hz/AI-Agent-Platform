import { platformPath } from "./auth";
import type {
  ArtifactVersion,
  AttachmentState,
  ConversationAttachment,
  ConversationCitation,
  ConversationReadState,
  SearchRecovery,
} from "./conversationTypes";


const ATTACHMENT_STATES = new Set<AttachmentState>([
  "uploading", "validating", "scanning", "ready", "quarantined", "rejected", "deleted",
]);
const ATTACHMENT_KEYS = new Set([
  "attachment_id", "conversation_id", "original_name", "declared_mime",
  "detected_mime", "size_bytes", "state", "created_at", "retained_until",
]);
const MESSAGE_ATTACHMENT_KEYS = new Set([
  "attachment_id", "conversation_id", "source", "display_name", "detected_mime",
  "size_bytes", "state", "created_at", "retained_until", "processing_coverage",
  "availability_reason",
]);
const UPLOAD_KEYS = new Set([
  "upload_id", "attachment_id", "conversation_id", "original_name", "declared_mime",
  "declared_size", "state", "uploaded_bytes", "expires_at",
]);
const TICKET_KEYS = new Set(["ticket", "expires_at", "content_path"]);
const CITATION_KEYS = new Set([
  "citation_key", "title", "url", "site", "retrieved_at", "supports",
]);
const ARTIFACT_VERSION_KEYS = new Set([
  "artifact_key", "version_no", "producer_version_id", "current", "status", "attachment",
]);
const READ_STATE_KEYS = new Set([
  "conversation_id", "last_read_message_seq", "last_read_at",
]);
const SEARCH_RECOVERY_KEYS = new Set([
  "status", "attempt_count", "last_attempt_at", "resumable", "coverage_note",
]);
const COVERAGE_KEYS = new Set(["coverage", "download", "inline_preview"]);
const NORMALIZED_COVERAGE_KEYS = new Set(["pages", "sheets", "slides", "ocr_complete"]);

export interface AttachmentUpload {
  uploadId: string;
  attachmentId: string;
  conversationId: string | null;
  displayName: string;
  declaredMime: string;
  declaredSize: number;
  state: AttachmentState;
  uploadedBytes: number;
  expiresAt: string;
}

export interface AttachmentTicket {
  ticket: string;
  expiresAt: string;
  contentPath: string;
}

export class AttachmentApiError extends Error {
  constructor(public status: number, public detail: unknown = null) {
    super(`Attachment API ${status}`);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: ReadonlySet<string>): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isState(value: unknown): value is AttachmentState {
  return typeof value === "string" && ATTACHMENT_STATES.has(value as AttachmentState);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function parseCoverage(value: unknown): ConversationAttachment["coverage"] {
  if (value === null) return null;
  if (isObject(value) && hasExactKeys(value, COVERAGE_KEYS)
    && isString(value.coverage)
    && typeof value.download === "boolean"
    && typeof value.inline_preview === "boolean") {
    return {
      pages: value.coverage === "first_page" ? 1 : null,
      sheets: null,
      slides: null,
      ocrComplete: value.coverage === "ocr_complete" ? true : null,
    };
  }
  if (isObject(value) && hasExactKeys(value, NORMALIZED_COVERAGE_KEYS)
    && [value.pages, value.sheets, value.slides].every(
      (item) => item === null || isNonNegativeInteger(item),
    )
    && (value.ocr_complete === null || typeof value.ocr_complete === "boolean")) {
    return {
      pages: value.pages as number | null,
      sheets: value.sheets as number | null,
      slides: value.slides as number | null,
      ocrComplete: value.ocr_complete as boolean | null,
    };
  }
  throw new Error("Attachment response invalid");
}

function preview(
  attachmentId: string,
  detectedMime: string | null,
  state: AttachmentState,
  allowed: boolean,
): ConversationAttachment["preview"] {
  if (state !== "ready" || !allowed || detectedMime === null
    || !(detectedMime.startsWith("image/") || detectedMime === "application/pdf")) return null;
  return { attachmentId, detectedMime };
}

export function parseConversationAttachment(value: unknown): ConversationAttachment {
  if (!isObject(value)) throw new Error("Attachment response invalid");
  const general = hasExactKeys(value, ATTACHMENT_KEYS);
  const projected = hasExactKeys(value, MESSAGE_ATTACHMENT_KEYS);
  if (!general && !projected) throw new Error("Attachment response invalid");

  const attachmentId = value.attachment_id;
  const conversationId = value.conversation_id;
  const displayName = general ? value.original_name : value.display_name;
  const source = general ? "user" : value.source;
  const coverageValue = general ? null : value.processing_coverage;
  const coverage = parseCoverage(coverageValue);
  if (!isString(attachmentId)
    || !isNullableString(conversationId)
    || (source !== "user" && source !== "agent")
    || !isString(displayName)
    || (general && !isString(value.declared_mime))
    || !isNullableString(value.detected_mime)
    || !isNonNegativeInteger(value.size_bytes)
    || !isState(value.state)
    || !isString(value.created_at)
    || !isString(value.retained_until)
    || (!general && !isNullableString(value.availability_reason))) {
    throw new Error("Attachment response invalid");
  }
  const allowPreview = general
    || (isObject(coverageValue) && coverageValue.inline_preview === true);
  return {
    attachmentId,
    conversationId,
    source,
    displayName,
    detectedMime: value.detected_mime,
    sizeBytes: value.size_bytes,
    sha256: null,
    state: value.state,
    stateReason: general ? null : value.availability_reason as string | null,
    createdAt: value.created_at,
    retainedUntil: value.retained_until,
    preview: preview(attachmentId, value.detected_mime, value.state, allowPreview),
    coverage,
  };
}

export function parseConversationCitation(value: unknown): ConversationCitation {
  if (!isObject(value) || !hasExactKeys(value, CITATION_KEYS)
    || !isString(value.citation_key) || !isString(value.title)
    || !isString(value.url) || !isString(value.site) || !isString(value.retrieved_at)
    || !Array.isArray(value.supports) || value.supports.length === 0
    || !value.supports.every(isString)) throw new Error("Citation response invalid");
  return {
    citationKey: value.citation_key,
    title: value.title,
    url: value.url,
    site: value.site,
    retrievedAt: value.retrieved_at,
    supports: [...value.supports],
  };
}

export function parseArtifactVersion(value: unknown): ArtifactVersion {
  if (!isObject(value) || !hasExactKeys(value, ARTIFACT_VERSION_KEYS)
    || !isString(value.artifact_key) || !isPositiveInteger(value.version_no)
    || !isString(value.producer_version_id) || typeof value.current !== "boolean"
    || !["processing", "ready", "failed"].includes(String(value.status))) {
    throw new Error("Artifact version response invalid");
  }
  let attachment: ConversationAttachment | null;
  try {
    attachment = value.attachment === null ? null : parseConversationAttachment(value.attachment);
  } catch {
    throw new Error("Artifact version response invalid");
  }
  if ((value.status === "ready") !== (attachment !== null)) {
    throw new Error("Artifact version response invalid");
  }
  return {
    artifactKey: value.artifact_key,
    versionNo: value.version_no,
    producerVersionId: value.producer_version_id,
    current: value.current,
    status: value.status as ArtifactVersion["status"],
    attachment,
  };
}

export function parseConversationReadState(value: unknown): ConversationReadState {
  if (!isObject(value) || !hasExactKeys(value, READ_STATE_KEYS)
    || !isString(value.conversation_id) || !isNonNegativeInteger(value.last_read_message_seq)
    || !isString(value.last_read_at)) throw new Error("Read state response invalid");
  return {
    conversationId: value.conversation_id,
    lastReadMessageSeq: value.last_read_message_seq,
    lastReadAt: value.last_read_at,
  };
}

export function parseSearchRecovery(value: unknown): SearchRecovery {
  if (!isObject(value) || !hasExactKeys(value, SEARCH_RECOVERY_KEYS)
    || !["unavailable", "no_results", "partial"].includes(String(value.status))
    || !isPositiveInteger(value.attempt_count) || !isString(value.last_attempt_at)
    || typeof value.resumable !== "boolean" || !isNullableString(value.coverage_note)
    || (value.status === "no_results" && value.resumable)
  ) throw new Error("Search recovery response invalid");
  return {
    status: value.status as SearchRecovery["status"],
    attemptCount: value.attempt_count,
    lastAttemptAt: value.last_attempt_at,
    resumable: value.resumable,
    coverageNote: value.coverage_note,
  };
}

function parseUpload(value: unknown): AttachmentUpload {
  if (!isObject(value) || !hasExactKeys(value, UPLOAD_KEYS)
    || !isString(value.upload_id) || !isString(value.attachment_id)
    || !isNullableString(value.conversation_id) || !isString(value.original_name)
    || !isString(value.declared_mime) || !isPositiveInteger(value.declared_size)
    || !isState(value.state) || !isNonNegativeInteger(value.uploaded_bytes)
    || value.uploaded_bytes > value.declared_size || !isString(value.expires_at)) {
    throw new Error("Attachment upload response invalid");
  }
  return {
    uploadId: value.upload_id,
    attachmentId: value.attachment_id,
    conversationId: value.conversation_id,
    displayName: value.original_name,
    declaredMime: value.declared_mime,
    declaredSize: value.declared_size,
    state: value.state,
    uploadedBytes: value.uploaded_bytes,
    expiresAt: value.expires_at,
  };
}

async function responseDetail(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}

async function checked(response: Response): Promise<Response> {
  if (!response.ok) throw new AttachmentApiError(response.status, await responseDetail(response));
  return response;
}

function writeHeaders(csrfToken: string, json = false): Record<string, string> {
  return {
    Accept: "application/json",
    ...(json ? { "Content-Type": "application/json" } : {}),
    "X-CSRF-Token": csrfToken,
  };
}

export async function beginAttachmentUpload(
  conversationId: string,
  file: File,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<AttachmentUpload> {
  if (!isString(conversationId) || !(file instanceof File) || file.size <= 0) {
    throw new Error("Attachment upload request invalid");
  }
  const response = await checked(await fetch(platformPath("/api/v1/attachments/uploads"), {
    method: "POST", credentials: "include", signal, headers: writeHeaders(csrfToken, true),
    body: JSON.stringify({
      conversation_id: conversationId,
      original_name: file.name,
      declared_mime: file.type || "application/octet-stream",
      declared_size: file.size,
    }),
  }));
  const result = parseUpload(await response.json());
  if (result.conversationId !== conversationId || result.displayName !== file.name
    || result.declaredSize !== file.size) throw new Error("Attachment upload response invalid");
  return result;
}

export async function uploadAttachmentContent(
  uploadId: string,
  file: File,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<AttachmentUpload> {
  const response = await checked(await fetch(platformPath(
    `/api/v1/attachments/uploads/${encodeURIComponent(uploadId)}/content`,
  ), {
    method: "PUT", credentials: "include", signal,
    headers: { ...writeHeaders(csrfToken), "Content-Type": file.type || "application/octet-stream" },
    body: file,
  }));
  const result = parseUpload(await response.json());
  if (result.uploadId !== uploadId) throw new Error("Attachment upload response invalid");
  return result;
}

export async function completeAttachmentUpload(
  uploadId: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<ConversationAttachment> {
  const response = await checked(await fetch(platformPath(
    `/api/v1/attachments/uploads/${encodeURIComponent(uploadId)}/complete`,
  ), { method: "POST", credentials: "include", signal, headers: writeHeaders(csrfToken) }));
  return parseConversationAttachment(await response.json());
}

export async function listConversationAttachments(
  conversationId: string,
  signal?: AbortSignal,
): Promise<ConversationAttachment[]> {
  const response = await checked(await fetch(platformPath(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/attachments`,
  ), { credentials: "include", signal, headers: { Accept: "application/json" } }));
  const value: unknown = await response.json();
  if (!Array.isArray(value)) throw new Error("Attachment list response invalid");
  const result = value.map(parseConversationAttachment);
  if (result.some((item) => item.conversationId !== conversationId)) {
    throw new Error("Attachment list response invalid");
  }
  return result;
}

export async function issueAttachmentTicket(
  attachmentId: string,
  purpose: "preview" | "download",
  csrfToken: string,
  signal?: AbortSignal,
): Promise<AttachmentTicket> {
  const response = await checked(await fetch(platformPath(
    `/api/v1/attachments/${encodeURIComponent(attachmentId)}/ticket`,
  ), {
    method: "POST", credentials: "include", signal, headers: writeHeaders(csrfToken, true),
    body: JSON.stringify({ purpose }),
  }));
  const value: unknown = await response.json();
  if (!isObject(value) || !hasExactKeys(value, TICKET_KEYS)
    || !/^[A-Za-z0-9_-]{32,256}$/.test(String(value.ticket))
    || !isString(value.expires_at)
    || value.content_path !== `/api/v1/attachments/content/${value.ticket}`) {
    throw new Error("Attachment ticket response invalid");
  }
  return { ticket: String(value.ticket), expiresAt: value.expires_at, contentPath: value.content_path };
}

export async function deleteConversationAttachment(
  attachmentId: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<void> {
  await checked(await fetch(platformPath(`/api/v1/attachments/${encodeURIComponent(attachmentId)}`), {
    method: "DELETE", credentials: "include", signal, headers: writeHeaders(csrfToken),
  }));
}

export async function cancelAttachmentUpload(
  uploadId: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<void> {
  await checked(await fetch(platformPath(
    `/api/v1/attachments/uploads/${encodeURIComponent(uploadId)}`,
  ), { method: "DELETE", credentials: "include", signal, headers: writeHeaders(csrfToken) }));
}
