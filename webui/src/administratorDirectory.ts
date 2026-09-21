import { checked, platformPath, type ManagedUser } from "./auth";

export interface AdministratorUser extends ManagedUser { departments: string[] }
export interface AdministratorSearchResult { users: AdministratorUser[]; truncated: boolean }

function parseResult(value: unknown): AdministratorSearchResult {
  if (!value || typeof value !== "object") throw new Error("administrator response invalid");
  const payload = value as Record<string, unknown>;
  if (!Array.isArray(payload.users) || typeof payload.truncated !== "boolean") throw new Error("administrator response invalid");
  const keys = new Set(["internal_user_id", "display_name", "status", "role", "scopes", "departments"]);
  const users = payload.users.map((entry: unknown) => {
    if (!entry || typeof entry !== "object") throw new Error("administrator response invalid");
    const user = entry as Record<string, unknown>;
    if (Object.keys(user).some(key => !keys.has(key))
      || typeof user.internal_user_id !== "string" || !user.internal_user_id
      || typeof user.display_name !== "string" || !user.display_name
      || typeof user.status !== "string" || !user.status
      || !["member", "management_viewer", "platform_admin", "platform_owner"].includes(String(user.role))
      || !Array.isArray(user.departments) || user.departments.some(name => typeof name !== "string" || !name)
      || !Array.isArray(user.scopes) || user.scopes.some(scope => typeof scope !== "string" || !scope)) {
      throw new Error("administrator response invalid");
    }
    return user as unknown as AdministratorUser;
  });
  return { users, truncated: payload.truncated };
}

async function readDirectory(params: URLSearchParams, signal?: AbortSignal): Promise<AdministratorSearchResult> {
  const response = await fetch(platformPath(`/api/v1/manage/users?${params}`), {
    credentials: "include", headers: { Accept: "application/json" }, signal,
  });
  await checked(response);
  return parseResult(await response.json());
}

export async function listAdministratorUsers(): Promise<AdministratorUser[]> {
  const result = await readDirectory(new URLSearchParams({ view: "administrators" }));
  if (result.truncated || result.users.some(user => !["platform_owner", "platform_admin"].includes(user.role))) {
    throw new Error("administrator response invalid");
  }
  return result.users;
}

export async function searchAdministratorCandidates(query: string, signal?: AbortSignal): Promise<AdministratorSearchResult> {
  if (!query.trim()) return { users: [], truncated: false };
  const result = await readDirectory(new URLSearchParams({ view: "candidates", q: query.trim(), limit: "20" }), signal);
  if (result.users.some(user => user.role !== "member" || user.status !== "active")) throw new Error("administrator response invalid");
  return result;
}
