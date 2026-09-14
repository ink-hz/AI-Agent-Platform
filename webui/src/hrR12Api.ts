import { platformPath } from "./auth";
import type { HrDownloadTicket, HrPositionResources, HrOfficialPositionDownload, HrOfficialPositionVersion } from "./hrR12Types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;

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
function nonnegative(value: unknown): number { if (!Number.isSafeInteger(value) || Number(value) < 0) throw new Error("HR R1.2 response invalid"); return Number(value); }
function object(value: unknown): Record<string, unknown> { if (!record(value)) throw new Error("HR R1.2 response invalid"); return value; }
function stringList(value: unknown): string[] { if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) throw new Error("HR R1.2 response invalid"); return [...value]; }
function items(value: unknown): unknown[] { if (!record(value) || !Array.isArray(value.items)) throw new Error("HR R1.2 list response invalid"); return value.items; }
function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort(); const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

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
    return artifact ? { ...base, artifactId: identifier(raw.artifact_id), artifactVersionId: identifier(raw.artifact_version_id), artifactVersion: positive(raw.artifact_version) } : base;
  };
  return { materials: value.materials.map((raw) => item(raw)), artifacts: value.artifacts.map((raw) => item(raw, true)) } as HrPositionResources;
}
function ticket(value: unknown): HrDownloadTicket {
  const raw = object(value);
  if (typeof raw.expires_at !== "string" || typeof raw.content_path !== "string" || !/^\/api\/v1\/attachments\/content\/[A-Za-z0-9_-]{32,256}$/.test(raw.content_path)) throw new Error("HR R1.2 ticket response invalid");
  return { contentPath: raw.content_path, expiresAt: raw.expires_at };
}
function officialVersion(value: unknown): HrOfficialPositionVersion {
  const raw = object(value);
  const keys = [
    "official_position_version_id", "position_id", "official_job_id", "title", "department", "locations",
    "category", "subcategory", "headcount", "degree", "employment_type", "salary", "duty", "requirement",
    "source_version", "source_changed_at", "source_snapshot_at", "content_hash", "first_observed_at",
    "last_observed_at", "official_status", "status_reason", "consecutive_misses", "official_status_code", "created_at",
  ];
  if (!exactKeys(raw, keys) || typeof raw.content_hash !== "string" || !SHA256.test(raw.content_hash)) {
    throw new Error("HR official position response invalid");
  }
  return {
    officialVersionId: identifier(raw.official_position_version_id), positionId: identifier(raw.position_id),
    officialJobId: string(raw.official_job_id), title: string(raw.title), department: nullableString(raw.department),
    locations: stringList(raw.locations), category: string(raw.category), subcategory: nullableString(raw.subcategory),
    headcount: nonnegative(raw.headcount), degree: nullableString(raw.degree), employmentType: string(raw.employment_type),
    salary: string(raw.salary), duty: string(raw.duty), requirement: string(raw.requirement),
    officialStatus: string(raw.official_status), statusReason: string(raw.status_reason),
    sourceVersion: string(raw.source_version), sourceChangedAt: string(raw.source_changed_at),
    sourceSnapshotAt: string(raw.source_snapshot_at), contentHash: raw.content_hash,
    firstObservedAt: string(raw.first_observed_at), lastObservedAt: string(raw.last_observed_at),
    consecutiveMisses: nonnegative(raw.consecutive_misses), officialStatusCode: nonnegative(raw.official_status_code),
    createdAt: string(raw.created_at),
  };
}
export function createHrR12Api(csrfToken: string) {
  const positionPath = (positionId: string, suffix = "") => `/api/hr/positions/${encodeURIComponent(identifier(positionId))}${suffix}`;
  const write = (path: string, requestId: string, body: unknown, signal?: AbortSignal) => request(path, mutation(csrfToken, requestId, body, signal));
  return {
    officialVersions(positionId: string, signal?: AbortSignal): Promise<HrOfficialPositionVersion[]> {
      return request(positionPath(positionId, "/official-versions"), { signal }).then((value) => items(value).map(officialVersion));
    },
    officialVersion(positionId: string, officialVersionId: string, signal?: AbortSignal): Promise<HrOfficialPositionVersion> {
      return request(positionPath(positionId, `/official-versions/${encodeURIComponent(identifier(officialVersionId))}`), { signal }).then(officialVersion);
    },
    async downloadOfficialVersion(positionId: string, officialVersionId: string, signal?: AbortSignal): Promise<HrOfficialPositionDownload> {
      const response = await fetch(platformPath(positionPath(positionId, `/official-versions/${encodeURIComponent(identifier(officialVersionId))}/export`)), {
        credentials: "same-origin", headers: { Accept: "text/markdown" }, signal,
      });
      if (!response.ok) throw new HrR12ApiError(response.status);
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? "official-position.md";
      return { blob: await response.blob(), filename };
    },
    resources(positionId: string, signal?: AbortSignal): Promise<HrPositionResources> { return request(positionPath(positionId, "/resources"), { signal }).then(resources); },
    downloadResource(positionId: string, attachmentId: string, requestId: string, purpose: "preview" | "download" = "download", signal?: AbortSignal): Promise<HrDownloadTicket> { return write(positionPath(positionId, `/resources/${encodeURIComponent(identifier(attachmentId))}/ticket`), requestId, { purpose }, signal).then(ticket); },
  };
}
export type HrR12Api = ReturnType<typeof createHrR12Api>;
