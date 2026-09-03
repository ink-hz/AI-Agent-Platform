import {
  ManagementMutationIndeterminate,
  PlatformApiError,
  platformPath,
  type Account,
} from "./auth";


export interface FaeAccessGrant {
  grant_id: string;
  internal_user_id: string;
  display_name: string;
  status: string;
  permission: "manager";
  created_at: string;
  row_version: number;
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}


function uuid(value: unknown): value is string {
  return typeof value === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}


function timestamp(value: unknown): value is string {
  return typeof value === "string"
    && value.length > 0
    && Number.isFinite(Date.parse(value));
}


function parseGrant(value: unknown): FaeAccessGrant {
  if (!isObject(value) || !exactKeys(value, [
    "grant_id",
    "internal_user_id",
    "display_name",
    "status",
    "permission",
    "created_at",
    "row_version",
  ])
    || !uuid(value.grant_id)
    || !uuid(value.internal_user_id)
    || typeof value.display_name !== "string"
    || !value.display_name
    || typeof value.status !== "string"
    || !value.status
    || value.permission !== "manager"
    || !timestamp(value.created_at)
    || !Number.isSafeInteger(value.row_version)
    || Number(value.row_version) < 0) {
    throw new Error("FAE access response invalid");
  }
  return value as unknown as FaeAccessGrant;
}


async function responseJson(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}


async function checked(response: Response, requestId?: string): Promise<unknown> {
  const value = await responseJson(response);
  if (response.ok) return value;
  if (response.status === 503) {
    const detail = isObject(value) && isObject(value.detail) ? value.detail : null;
    if (
      detail?.code === "management_mutation_indeterminate"
      && typeof detail.request_id === "string"
      && detail.request_id
    ) {
      if (requestId !== undefined && detail.request_id !== requestId) {
        throw new Error("FAE access response invalid");
      }
      throw new ManagementMutationIndeterminate(detail.request_id, value);
    }
  }
  throw new PlatformApiError(response.status, value);
}


function csrfHeaders(account: Account): HeadersInit {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-CSRF-Token": account.csrf_token,
  };
}


export const faeAccessApi = {
  async list(): Promise<FaeAccessGrant[]> {
    const response = await fetch(platformPath("/api/v1/manage/fae-workbench/grants"), {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    const value = await checked(response);
    if (!isObject(value) || !exactKeys(value, ["grants"]) || !Array.isArray(value.grants)) {
      throw new Error("FAE access response invalid");
    }
    return value.grants.map(parseGrant);
  },

  async grant(
    account: Account,
    displayName: string,
    requestId: string,
  ): Promise<void> {
    const response = await fetch(platformPath("/api/v1/manage/fae-workbench/grants"), {
      method: "POST",
      credentials: "include",
      headers: csrfHeaders(account),
      body: JSON.stringify({
        display_name: displayName,
        reason: "fae_workbench_access_approved",
        request_id: requestId,
      }),
    });
    const value = await checked(response, requestId);
    if (!isObject(value) || value.status !== "ok") {
      throw new Error("FAE access response invalid");
    }
  },

  async revoke(
    account: Account,
    grant: FaeAccessGrant,
    requestId: string,
  ): Promise<void> {
    const response = await fetch(
      platformPath(`/api/v1/manage/fae-workbench/grants/${encodeURIComponent(grant.internal_user_id)}`),
      {
        method: "DELETE",
        credentials: "include",
        headers: csrfHeaders(account),
        body: JSON.stringify({
          reason: "fae_workbench_access_revoked",
          request_id: requestId,
          expected_row_version: grant.row_version,
        }),
      },
    );
    const value = await checked(response, requestId);
    if (!isObject(value) || value.status !== "ok") {
      throw new Error("FAE access response invalid");
    }
  },
};
