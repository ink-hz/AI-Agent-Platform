import { platformPath } from "../auth";

export type NativeRequirementSummary = {
  id: number; title: string; status: string; priority: number; version: number;
  opened_by: string; opened_by_name: string; assigned_to: string; assigned_to_name: string;
  opened_date: string; assigned_date: string; business_proposer: string | null;
  source_module: string | null; q4_source: boolean;
};

export type NativeRequirementDetail = NativeRequirementSummary & {
  spec: string; verify: string; source_note: string; edit_token: string; device_platform?: string;
  source: null | { proposer_name: string | null; responsible_raw: string; planned_start: string | null;
    planned_end: string | null; source_version: string; source_risk: string; source_status: string;
    source_note: string; module: string; device_platform?: string };
  reviewer: string[]; tasks: Array<{ id: number; name?: string }>;
  actions: Array<{ id: number; action: string; actor: string; actor_name: string; date: string; comment: string }>;
};

export type NativeTask = {
  id: number; name: string; status: string; assignee: string; assignee_name: string;
  story_id: number; opened_date: string; planned_start: string; deadline: string;
};
export type NativeTaskDetail = NativeTask & { description: string; execution_id: number; execution_name: string;
  actions: NativeRequirementDetail["actions"] };

export type NativePage<T> = { items: T[]; page: number; total: number };
export type NativeMember = { account: string; name: string; label?: string };
export type NativeRequirementInput = {
  request_id: string; title: string; scenario: string; device_platform: string; acceptance: string;
  priority: number; business_proposer?: string | null; assigned_to?: string | null; reviewer?: string | null;
};
export type NativeRequirementEdit = Omit<NativeRequirementInput, "request_id" | "assigned_to" | "reviewer" | "device_platform"> & {
  expected_version: number; expected_token: string; device_platform: string | null;
};
export type NativeCreateResult = { status: "created"; story_id: number; requirement: NativeRequirementDetail } |
  { status: "reconciliation_required"; request_id: string };
export type NativePendingResult = NativeCreateResult | { status: "none" };

export interface NativeSdkApi {
  requirements(filter: { view: "q4" | "all"; page: number; query: string; status: string; owner?: string }): Promise<NativePage<NativeRequirementSummary>>;
  requirement(id: number): Promise<NativeRequirementDetail>;
  myTasks(page: number): Promise<NativePage<NativeTask>>;
  task(id: number): Promise<NativeTaskDetail>;
  members(): Promise<{ items: NativeMember[] }>;
  createRequirement(input: NativeRequirementInput, csrfToken: string): Promise<NativeCreateResult>;
  pendingCreate(): Promise<NativePendingResult>;
  editRequirement(id: number, input: NativeRequirementEdit, csrfToken: string): Promise<NativeRequirementDetail>;
  assign(id: number, input: { expected_version: number; expected_token: string; assigned_to: string }, csrfToken: string): Promise<NativeRequirementDetail>;
  review(id: number, input: { expected_version: number; expected_token: string; decision: "pass" | "reject"; comment?: string; closed_reason?: string }, csrfToken: string): Promise<NativeRequirementDetail>;
}

export class NativeSdkApiError extends Error {
  constructor(public status: number, public detail: string) { super(detail); }
}

async function request<T>(path: string, method = "GET", payload?: object, csrfToken?: string): Promise<T> {
  const response = await fetch(platformPath(`/api/v1/sdk/native${path}`), {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(payload ? { "Content-Type": "application/json", "X-CSRF-Token": csrfToken ?? "" } : {}),
    },
    ...(payload ? { body: JSON.stringify(payload) } : {}),
  });
  const body = await response.json().catch(() => null) as Record<string, unknown> | null;
  if (!response.ok) throw new NativeSdkApiError(response.status,
    typeof body?.detail === "string" ? body.detail : "sdk_native_request_failed");
  return body as T;
}

export const nativeSdkApi: NativeSdkApi = {
  requirements: ({ view, page, query, status, owner = "" }) => {
    const params = new URLSearchParams({ view, page: String(page) });
    if (query) params.set("query", query);
    if (status) params.set("status", status);
    if (owner) params.set("owner", owner);
    return request(`/requirements?${params}`);
  },
  requirement: id => request(`/requirements/${id}`),
  myTasks: page => request(`/my-tasks?page=${page}`),
  task: id => request(`/tasks/${id}`),
  members: () => request("/members"),
  createRequirement: (input, csrfToken) => request("/requirements", "POST", input, csrfToken),
  pendingCreate: () => request("/requirements/pending-create"),
  editRequirement: (id, input, csrfToken) => request(`/requirements/${id}`, "PUT", input, csrfToken),
  assign: (id, input, csrfToken) => request(`/requirements/${id}/assign`, "POST", input, csrfToken),
  review: (id, input, csrfToken) => request(`/requirements/${id}/review`, "POST", input, csrfToken),
};
