import { platformPath } from "../auth";

export type SdkRole = "viewer" | "contributor" | "reviewer" | "release_manager";
export type SdkMe = { role: SdkRole | null; can_manage: boolean; read_only: boolean };
export type RequirementSummary = { id: number; title: string; status: string };
export type RequirementList = { items: RequirementSummary[]; page: number; total: number | null };
export type RequirementDetail = {
  story: { id: string; product: string; title: string; spec?: string; verify?: string; status?: string };
  reviews: Array<{ actor_id: string; decision: string; reason: string; created_at?: string }>;
  tasks: Array<{ story_id: number; task_id: number }>;
};
export type RequirementInput = {
  request_id: string; title: string; scenario: string; device_platform: string; acceptance: string;
};
export type ReconciliationIntent = { actor_id: string; request_id: string; kind: string; story_id: number | null; created_at?: string };
export type Target = { os: string; arch: string };
export type Manifest = { packaging_sha: string; sdk_version: string; script_path: string; modules: Record<string, string> };
export type CandidateSummary = { id: number; story_id: number; story_version: string; evidence_version: number; creator_id: string; created_at: string };
export type BuildEvidence = { id: number; os: string; arch: string; status: string; command: string; log_uri: string; artifact_name: string | null; artifact_sha256: string | null; artifact_size: number | null; artifact_uri: string | null; created_at: string };
export type TestEvidence = { id: number; build_id: number; suite: string; status: string; passed: number; failed: number; skipped: number; report_uri: string; device: string; firmware: string; created_at: string };
export type CandidateDetail = CandidateSummary & { task_ids: number[]; manifest: Manifest; targets: Target[]; builds: BuildEvidence[]; tests: TestEvidence[]; approvals: Array<{ id: number; evidence_version: number; actor_id: string; note: string; created_at: string }>; releases: Array<{ id: number; channel: string; released_at: string; rollback_uri: string; artifact_sha256s: string[]; created_at: string }> };
export type Gate = { ready: boolean; reasons: string[]; evidence_version: number; approved: boolean; approval_id: number | null };
export type CandidateInput = { request_id: string; story_id: number; task_ids: number[]; manifest: Manifest; targets: Target[] };
export type BuildInput = { request_id: string; os: string; arch: string; status: "success" | "failed" | "cancelled" | "timeout"; command: string; log_uri: string; artifact_name: string | null; artifact_sha256: string | null; artifact_size: number | null; artifact_uri: string | null };
export type TestInput = { request_id: string; suite: "unit" | "hardware"; status: "passed" | "failed" | "blocked" | "skipped"; passed: number; failed: number; skipped: number; report_uri: string; device: string; firmware: string };

export class SdkApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

async function request<T>(path: string, payload?: object, csrfToken?: string): Promise<T> {
  const response = await fetch(platformPath(`/api/v1/sdk${path}`), {
    credentials: "same-origin",
    method: payload ? "POST" : "GET",
    headers: {
      Accept: "application/json",
      ...(payload ? { "Content-Type": "application/json", "X-CSRF-Token": csrfToken ?? "" } : {}),
    },
    ...(payload ? { body: JSON.stringify(payload) } : {}),
  });
  const body = await response.json().catch(() => null) as Record<string, unknown> | null;
  if (!response.ok) throw new SdkApiError(response.status, typeof body?.detail === "string" ? body.detail : "sdk_request_failed");
  return body as T;
}

export interface SdkApi {
  me(): Promise<SdkMe>;
  requirements(page?: number): Promise<RequirementList>;
  requirement(storyId: number): Promise<RequirementDetail>;
  createRequirement(input: RequirementInput, csrfToken: string): Promise<{ id: number }>;
  createTask(storyId: number, input: { request_id: string; name: string; description: string }, csrfToken: string): Promise<{ id: number }>;
  review(storyId: number, input: { request_id: string; decision: "approved" | "rejected" | "needs_changes"; reason: string }, csrfToken: string): Promise<unknown>;
  members(): Promise<{ items: Array<{ internal_user_id: string; role: SdkRole }> }>;
  grantMember(input: { request_id: string; internal_user_id: string; role: SdkRole }, csrfToken: string): Promise<unknown>;
  reconciliation(): Promise<{ items: ReconciliationIntent[] }>;
  bindReconciliation(intent: ReconciliationIntent, input: { request_id: string; external_id: number }, csrfToken: string): Promise<{ id: number }>;
  candidates(page?: number): Promise<{ items: CandidateSummary[]; page: number; total: number }>;
  candidate(id: number): Promise<CandidateDetail>;
  createCandidate(input: CandidateInput, csrfToken: string): Promise<{ id: number }>;
  addBuild(id: number, input: BuildInput, csrfToken: string): Promise<{ id: number }>;
  addTest(buildId: number, input: TestInput, csrfToken: string): Promise<{ id: number }>;
  gate(id: number): Promise<Gate>;
  approve(id: number, input: { request_id: string; note: string }, csrfToken: string): Promise<{ id: number; evidence_version: number }>;
  release(id: number, input: { request_id: string; channel: string; rollback_uri: string; released_at: string }, csrfToken: string): Promise<{ id: number }>;
}

export const sdkApi: SdkApi = {
  me: () => request("/me"),
  requirements: (page = 1) => request(`/requirements?page=${page}`),
  requirement: storyId => request(`/requirements/${storyId}`),
  createRequirement: (input, csrf) => request("/requirements", input, csrf),
  createTask: (storyId, input, csrf) => request(`/requirements/${storyId}/tasks`, input, csrf),
  review: (storyId, input, csrf) => request(`/requirements/${storyId}/reviews`, input, csrf),
  members: () => request("/members"),
  grantMember: (input, csrf) => request("/members", input, csrf),
  reconciliation: () => request("/reconciliation"),
  bindReconciliation: (intent, input, csrf) => request(`/reconciliation/${encodeURIComponent(intent.actor_id)}/${encodeURIComponent(intent.request_id)}/bind`, input, csrf),
  candidates: (page = 1) => request(`/candidates?page=${page}`),
  candidate: id => request(`/candidates/${id}`),
  createCandidate: (input, csrf) => request("/candidates", input, csrf),
  addBuild: (id, input, csrf) => request(`/candidates/${id}/builds`, input, csrf),
  addTest: (buildId, input, csrf) => request(`/artifacts/${buildId}/tests`, input, csrf),
  gate: id => request(`/candidates/${id}/gate`),
  approve: (id, input, csrf) => request(`/candidates/${id}/approval`, input, csrf),
  release: (id, input, csrf) => request(`/candidates/${id}/release`, input, csrf),
};
