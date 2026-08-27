export type PlatformRole =
  | "member"
  | "management_viewer"
  | "platform_admin"
  | "platform_owner";
export type DirectoryFreshness = "fresh" | "warning" | "hard_stale";
export type TrustedGender = "male" | "female" | null;

export interface Account {
  internal_user_id: string;
  display_name: string;
  role: PlatformRole;
  departments: string[];
  gender: TrustedGender;
  observation_agent_ids: string[];
  directory_freshness: DirectoryFreshness;
  hard_stale_read_only: boolean;
  csrf_token: string;
}

export interface ManagedUser {
  internal_user_id: string;
  display_name: string;
  status: string;
  role: PlatformRole;
  scopes: string[];
}

export interface AdministratorMutation {
  readonly targetInternalUserId: string;
  readonly revoke: boolean;
  readonly requestId: string;
}

export interface GovernanceEvent {
  audit_event_id: string;
  event_type: string;
  result: string;
  reason_code: string;
  occurred_at: string;
}


export class PlatformApiError extends Error {
  constructor(public status: number, public detail: unknown = null) {
    super(`platform API ${status}`);
  }
}

export class AuthenticationRequired extends PlatformApiError {
  constructor() { super(401); }
}

export class PermissionDenied extends PlatformApiError {
  constructor() { super(403); }
}

export class DirectoryUnavailable extends PlatformApiError {
  constructor(detail: unknown = null) { super(503, detail); }
}

export class ManagementMutationIndeterminate extends PlatformApiError {
  constructor(public requestId: string, detail: unknown) {
    super(503, detail);
    this.name = "ManagementMutationIndeterminate";
  }
}

export class IdentityDisabled extends PlatformApiError {
  constructor() { super(404); }
}

const PREVIEW_PREFIX = "/_preview/dingtalk-r1";
const ACCOUNT_KEYS = new Set([
  "internal_user_id", "display_name", "role", "departments", "gender", "observation_agent_ids",
  "real_name", "mobile", "primary_department",
  "directory_freshness", "hard_stale_read_only", "csrf_token",
]);
const MANAGED_USER_KEYS = new Set([
  "internal_user_id", "display_name", "status", "role", "scopes",
]);
const PLATFORM_ROLES: readonly PlatformRole[] = [
  "member", "management_viewer", "platform_admin", "platform_owner",
];
const ACCOUNT_TIMEOUT_MS = 5_000;
const ACCOUNT_RETRY_DELAY_MS = 200;


export function routePrefix(pathname?: string): string {
  const selected = pathname ?? (typeof window === "undefined" ? "/" : window.location.pathname);
  return selected === PREVIEW_PREFIX || selected.startsWith(`${PREVIEW_PREFIX}/`)
    ? PREVIEW_PREFIX
    : "";
}


export function platformPath(path: string, prefix = routePrefix()): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${prefix}${normalized}` || "/";
}


export function localPathname(pathname?: string): string {
  const selected = pathname ?? (typeof window === "undefined" ? "/" : window.location.pathname);
  const prefix = routePrefix(selected);
  if (!prefix) return selected;
  const local = selected.slice(prefix.length);
  return local || "/";
}


export type LoginReturnPath =
  | "/"
  | "/account"
  | "/missions"
  | `/missions/${string}`
  | "/conversations"
  | `/conversations/${string}`
  | "/agents"
  | "/agents/voc/workspace"
  | `/agents/${string}`
  | "/ai-notes"
  | `/ai-notes/${string}/${string}`
  | "/office/"
  | "/admin"
  | "/admin/"
  | `/admin/${string}`;


function safeLoginReturnPath(value: string): boolean {
  if (!value.startsWith("/") || value.startsWith("//") || /[?#\\%\u0000-\u001f\u007f]/.test(value)) return false;
  if (value === "/" || value === "/account" || value === "/missions" || value === "/conversations" || value === "/agents" || value === "/agents/voc/workspace" || value === "/ai-notes") return true;
  if (/^\/missions\/[0-9a-fA-F-]{36}$/.test(value)) return true;
  if (/^\/conversations\/[0-9a-fA-F-]{36}$/.test(value)) return true;
  if (/^\/agents\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) return true;
  if (/^\/ai-notes\/[a-z0-9][a-z0-9-]{0,63}\/[a-z0-9][a-z0-9-]{0,127}$/.test(value)) return true;
  return value === "/office/" || value === "/admin/" || value === "/admin"
    || /^\/admin\/(?:overview|review|activity|operations|identity|governance|voc|agents(?:\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:\/runtime)?)?|sessions(?:\/[A-Za-z0-9:._-]+)?)$/.test(value);
}


export function loginReturnPath(search: string): LoginReturnPath {
  const params = new URLSearchParams(search);
  const values = params.getAll("return_path");
  return values.length === 1 && safeLoginReturnPath(values[0]) ? values[0] as LoginReturnPath : "/";
}


export function identityShellEnabled(): boolean {
  if (typeof document === "undefined") return false;
  return document.querySelector<HTMLMetaElement>('meta[name="platform-identity-mode"]')?.content === "enabled";
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


function isPlatformRole(value: unknown): value is PlatformRole {
  return PLATFORM_ROLES.includes(value as PlatformRole);
}


function parseAccount(value: unknown): Account {
  if (!isObject(value) || Object.keys(value).some((key) => !ACCOUNT_KEYS.has(key))) {
    throw new Error("account response invalid");
  }
  const role = value.role;
  const freshness = value.directory_freshness;
  const departments = value.departments;
  const gender = value.gender;
  const scopes = value.observation_agent_ids;
  if (
    typeof value.internal_user_id !== "string" || !value.internal_user_id
    || typeof value.display_name !== "string" || !value.display_name
    || !isPlatformRole(role)
    || !Array.isArray(departments)
    || departments.some((department) => typeof department !== "string" || !department)
    || (gender !== "male" && gender !== "female" && gender !== null)
    || (value.real_name !== undefined && value.real_name !== null && typeof value.real_name !== "string")
    || (value.mobile !== undefined && value.mobile !== null && typeof value.mobile !== "string")
    || (value.primary_department !== undefined && value.primary_department !== null && typeof value.primary_department !== "string")
    || !Array.isArray(scopes) || scopes.some((scope) => typeof scope !== "string" || !scope)
    || !["fresh", "warning", "hard_stale"].includes(String(freshness))
    || typeof value.hard_stale_read_only !== "boolean"
    || typeof value.csrf_token !== "string"
  ) {
    throw new Error("account response invalid");
  }
  return {
    internal_user_id: value.internal_user_id,
    display_name: value.display_name,
    role: role as PlatformRole,
    departments: [...departments] as string[],
    gender: gender as TrustedGender,
    observation_agent_ids: [...scopes] as string[],
    directory_freshness: freshness as DirectoryFreshness,
    hard_stale_read_only: value.hard_stale_read_only,
    csrf_token: value.csrf_token,
  };
}


function parseManagedUser(value: unknown): ManagedUser {
  if (!isObject(value) || Object.keys(value).some((key) => !MANAGED_USER_KEYS.has(key))) {
    throw new Error("management response invalid");
  }
  if (
    typeof value.internal_user_id !== "string" || !value.internal_user_id
    || typeof value.display_name !== "string" || !value.display_name
    || typeof value.status !== "string" || !value.status
    || !isPlatformRole(value.role)
    || !Array.isArray(value.scopes)
    || value.scopes.some((scope) => typeof scope !== "string" || !scope)
  ) {
    throw new Error("management response invalid");
  }
  return {
    internal_user_id: value.internal_user_id,
    display_name: value.display_name,
    status: value.status,
    role: value.role,
    scopes: [...value.scopes] as string[],
  };
}


async function responseDetail(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}


async function checked(response: Response): Promise<Response> {
  if (response.status === 401) throw new AuthenticationRequired();
  if (response.status === 403) throw new PermissionDenied();
  if (response.status === 404) throw new IdentityDisabled();
  if (response.status === 503) {
    const detail = await responseDetail(response);
    if (
      isObject(detail)
      && isObject(detail.detail)
      && detail.detail.code === "management_mutation_indeterminate"
      && typeof detail.detail.request_id === "string"
      && detail.detail.request_id
    ) {
      throw new ManagementMutationIndeterminate(detail.detail.request_id, detail);
    }
    throw new DirectoryUnavailable(detail);
  }
  if (!response.ok) throw new PlatformApiError(response.status, await responseDetail(response));
  return response;
}


async function fetchAccount(prefix: string): Promise<Account> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), ACCOUNT_TIMEOUT_MS);
  try {
    const response = await fetch(platformPath("/api/v1/account", prefix), {
      credentials: "include",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    await checked(response);
    const contentType = response.headers.get("Content-Type") || "";
    if (!contentType.toLowerCase().includes("application/json")) throw new IdentityDisabled();
    return parseAccount(await response.json());
  } finally {
    globalThis.clearTimeout(timeout);
  }
}


function retryableAccountRead(error: unknown): boolean {
  if (error instanceof PlatformApiError) return error.status === 502 || error.status === 504;
  if (error instanceof TypeError) return true;
  return isObject(error) && error.name === "AbortError";
}


function accountRetryDelay(): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ACCOUNT_RETRY_DELAY_MS));
}


export async function loadAccount(prefix = routePrefix()): Promise<Account> {
  try {
    return await fetchAccount(prefix);
  } catch (error) {
    if (!retryableAccountRead(error)) throw error;
    await accountRetryDelay();
    return fetchAccount(prefix);
  }
}


export async function startQrLogin(returnPath: LoginReturnPath = "/"): Promise<string> {
  const response = await fetch(platformPath("/api/v1/auth/dingtalk/start"), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ return_path: platformPath(returnPath) }),
  });
  await checked(response);
  const value: unknown = await response.json();
  if (!isObject(value) || typeof value.authorization_url !== "string") {
    throw new Error("login response invalid");
  }
  const target = new URL(value.authorization_url);
  if (target.protocol !== "https:" || target.hostname !== "login.dingtalk.com") {
    throw new Error("login response invalid");
  }
  return target.toString();
}


export async function exchangeInClientCode(code: string, appId = "platform"): Promise<void> {
  const response = await fetch(platformPath("/api/v1/auth/dingtalk/in-client/exchange"), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ code, app_id: appId }),
  });
  await checked(response);
}


export function inClientLoginAvailable(): boolean {
  if (typeof navigator === "undefined") return false;
  return /DingTalk|AliApp\(DingTalk/i.test(navigator.userAgent);
}


async function loadPublicDingTalkConfig(returnPath: LoginReturnPath): Promise<{
  client_id: string; corp_id: string; app_id: string;
}> {
  const query = new URLSearchParams({ return_path: returnPath });
  const configResponse = await fetch(platformPath(`/api/v1/auth/dingtalk/config?${query.toString()}`), {
    credentials: "include", headers: { Accept: "application/json" },
  });
  await checked(configResponse);
  const config: unknown = await configResponse.json();
  if (!isObject(config) || Object.keys(config).some((key) => !["client_id", "corp_id", "app_id"].includes(key))
    || Object.keys(config).length !== 3
    || typeof config.client_id !== "string" || !config.client_id
    || typeof config.corp_id !== "string" || !config.corp_id
    || typeof config.app_id !== "string" || !/^[a-z][a-z0-9_-]{0,31}$/.test(config.app_id)) {
    throw new Error("DingTalk configuration invalid");
  }
  return { client_id: config.client_id, corp_id: config.corp_id, app_id: config.app_id };
}


export async function inClientLogin(returnPath: LoginReturnPath = "/"): Promise<void> {
  if (!inClientLoginAvailable()) throw new Error("DingTalk JSAPI unavailable");
  const { default: dd } = await import("dingtalk-jsapi");
  const config = await loadPublicDingTalkConfig(returnPath);
  const result = await dd.requestAuthCode({
    clientId: config.client_id,
    corpId: config.corp_id,
  });
  if (!result || typeof result.code !== "string" || !result.code) throw new Error("DingTalk authorization failed");
  await exchangeInClientCode(result.code, config.app_id);
}


export async function logoutAccount(csrfToken: string): Promise<void> {
  const response = await fetch(platformPath("/api/v1/auth/logout"), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "X-CSRF-Token": csrfToken },
  });
  await checked(response);
}


export async function listManagedUsers(): Promise<ManagedUser[]> {
  const response = await fetch(platformPath("/api/v1/manage/users"), {
    credentials: "include", headers: { Accept: "application/json" },
  });
  await checked(response);
  const payload: unknown = await response.json();
  if (!isObject(payload) || !Array.isArray(payload.users)) throw new Error("management response invalid");
  return payload.users.map(parseManagedUser);
}


export function createAdministratorMutation(
  user: ManagedUser,
  revoke: boolean,
): AdministratorMutation {
  return Object.freeze({
    targetInternalUserId: user.internal_user_id,
    revoke,
    requestId: crypto.randomUUID(),
  });
}


export async function changeAdministrator(
  account: Account,
  operation: AdministratorMutation,
): Promise<void> {
  const response = await fetch(platformPath(`/api/v1/manage/admins/${encodeURIComponent(operation.targetInternalUserId)}`), {
    method: operation.revoke ? "DELETE" : "POST",
    credentials: "include",
    headers: {
      Accept: "application/json", "Content-Type": "application/json",
      "X-CSRF-Token": account.csrf_token,
    },
    body: JSON.stringify({
      reason: operation.revoke ? "admin_access_revoked" : "admin_access_approved",
      request_id: operation.requestId,
    }),
  });
  await checked(response);
}


export async function changeViewer(
  account: Account,
  user: ManagedUser,
  reason: string,
): Promise<void> {
  const revoke = user.role === "management_viewer";
  const response = await fetch(platformPath(`/api/v1/manage/viewers/${encodeURIComponent(user.internal_user_id)}`), {
    method: revoke ? "DELETE" : "POST",
    credentials: "include",
    headers: {
      Accept: "application/json", "Content-Type": "application/json",
      "X-CSRF-Token": account.csrf_token,
    },
    body: JSON.stringify({ reason }),
  });
  await checked(response);
}


export async function changeObservationScope(
  account: Account,
  user: ManagedUser,
  agentId: string,
  reason: string,
  revoke = false,
): Promise<void> {
  const exactAgentId = agentId.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(exactAgentId)) {
    throw new Error("exact Agent ID required");
  }
  const response = await fetch(platformPath(
    `/api/v1/manage/viewers/${encodeURIComponent(user.internal_user_id)}/observations/${encodeURIComponent(exactAgentId)}`,
  ), {
    method: revoke ? "DELETE" : "PUT",
    credentials: "include",
    headers: {
      Accept: "application/json", "Content-Type": "application/json",
      "X-CSRF-Token": account.csrf_token,
    },
    body: JSON.stringify({ reason }),
  });
  await checked(response);
}


export async function listGovernanceAudit(): Promise<GovernanceEvent[]> {
  const response = await fetch(platformPath("/api/v1/manage/audit/governance"), {
    credentials: "include", headers: { Accept: "application/json" },
  });
  await checked(response);
  const payload: unknown = await response.json();
  if (!isObject(payload) || !Array.isArray(payload.events)) throw new Error("governance response invalid");
  return payload.events as GovernanceEvent[];
}
