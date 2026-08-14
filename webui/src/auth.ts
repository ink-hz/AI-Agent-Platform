export type PlatformRole = "member" | "management_viewer" | "platform_owner";
export type DirectoryFreshness = "fresh" | "warning" | "hard_stale";

export interface Account {
  internal_user_id: string;
  display_name: string;
  role: PlatformRole;
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
  constructor() { super(503); }
}

export class IdentityDisabled extends PlatformApiError {
  constructor() { super(404); }
}

const PREVIEW_PREFIX = "/_preview/dingtalk-r1";
const ACCOUNT_KEYS = new Set([
  "internal_user_id", "display_name", "role", "observation_agent_ids",
  "directory_freshness", "hard_stale_read_only", "csrf_token",
]);


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


export function identityShellEnabled(): boolean {
  if (typeof document === "undefined") return false;
  return document.querySelector<HTMLMetaElement>('meta[name="platform-identity-mode"]')?.content === "enabled";
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


function parseAccount(value: unknown): Account {
  if (!isObject(value) || Object.keys(value).some((key) => !ACCOUNT_KEYS.has(key))) {
    throw new Error("account response invalid");
  }
  const role = value.role;
  const freshness = value.directory_freshness;
  const scopes = value.observation_agent_ids;
  if (
    typeof value.internal_user_id !== "string" || !value.internal_user_id
    || typeof value.display_name !== "string" || !value.display_name
    || !["member", "management_viewer", "platform_owner"].includes(String(role))
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
    observation_agent_ids: [...scopes] as string[],
    directory_freshness: freshness as DirectoryFreshness,
    hard_stale_read_only: value.hard_stale_read_only,
    csrf_token: value.csrf_token,
  };
}


async function responseDetail(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}


async function checked(response: Response): Promise<Response> {
  if (response.status === 401) throw new AuthenticationRequired();
  if (response.status === 403) throw new PermissionDenied();
  if (response.status === 404) throw new IdentityDisabled();
  if (response.status === 503) throw new DirectoryUnavailable();
  if (!response.ok) throw new PlatformApiError(response.status, await responseDetail(response));
  return response;
}


export async function loadAccount(prefix = routePrefix()): Promise<Account> {
  const response = await fetch(platformPath("/api/v1/account", prefix), {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  await checked(response);
  const contentType = response.headers.get("Content-Type") || "";
  if (!contentType.toLowerCase().includes("application/json")) throw new IdentityDisabled();
  return parseAccount(await response.json());
}


export async function startQrLogin(returnPath = "/account"): Promise<string> {
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


export async function exchangeInClientCode(code: string): Promise<void> {
  const response = await fetch(platformPath("/api/v1/auth/dingtalk/in-client/exchange"), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  await checked(response);
}


export function inClientLoginAvailable(): boolean {
  if (typeof navigator === "undefined") return false;
  return /DingTalk|AliApp\(DingTalk/i.test(navigator.userAgent);
}


async function loadPublicDingTalkConfig(): Promise<{ client_id: string; corp_id: string }> {
  const configResponse = await fetch(platformPath("/api/v1/auth/dingtalk/config"), {
    credentials: "include", headers: { Accept: "application/json" },
  });
  await checked(configResponse);
  const config: unknown = await configResponse.json();
  if (!isObject(config) || Object.keys(config).some((key) => !["client_id", "corp_id"].includes(key))
    || typeof config.client_id !== "string" || !config.client_id
    || typeof config.corp_id !== "string" || !config.corp_id) {
    throw new Error("DingTalk configuration invalid");
  }
  return { client_id: config.client_id, corp_id: config.corp_id };
}


export async function inClientLogin(): Promise<void> {
  if (!inClientLoginAvailable()) throw new Error("DingTalk JSAPI unavailable");
  const { default: dd } = await import("dingtalk-jsapi");
  const config = await loadPublicDingTalkConfig();
  const result = await dd.requestAuthCode({
    clientId: config.client_id,
    corpId: config.corp_id,
  });
  if (!result || typeof result.code !== "string" || !result.code) throw new Error("DingTalk authorization failed");
  await exchangeInClientCode(result.code);
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
  return payload.users as ManagedUser[];
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
