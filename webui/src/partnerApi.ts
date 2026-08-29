import { platformPath } from "./auth";


export type PartnerStatus = "active" | "suspended" | "disabled";
export type PartnerBindingStatus = "pending" | "linked" | "rejected" | "expired";

export interface PartnerOrganization {
  partner_organization_id: string;
  display_name: string;
  status: PartnerStatus;
  created_at: string;
  updated_at: string;
  invalidated_at: string | null;
}

export interface PartnerOperator {
  partner_operator_id: string;
  subject_id: string;
  partner_organization_id: string;
  display_name: string;
  status: PartnerStatus;
  fae_grant_active: boolean;
  fae_granted_at: string | null;
  created_at: string;
  updated_at: string;
  invalidated_at: string | null;
}

export interface PartnerBindingRequest {
  binding_request_id: string;
  provider_kind: string;
  display_name: string | null;
  status: PartnerBindingStatus;
  verified_at: string;
  requested_at: string;
  expires_at: string;
  resolved_at: string | null;
  linked_partner_operator_id: string | null;
}

export interface PartnerSnapshot {
  organizations: PartnerOrganization[];
  operators: PartnerOperator[];
  bindingRequests: PartnerBindingRequest[];
}

export type PartnerMutationResult =
  | { kind: "organization"; projection: PartnerOrganization }
  | { kind: "operator"; projection: PartnerOperator }
  | { kind: "binding_request"; projection: PartnerBindingRequest };

export class PartnerApiError extends Error {
  constructor(public status: number, public detail: unknown = null) {
    super(`partner API ${status}`);
  }
}

export class PartnerMutationIndeterminate extends PartnerApiError {
  constructor(public requestId: string, detail: unknown = null) {
    super(503, detail);
    this.name = "PartnerMutationIndeterminate";
  }
}

export class PartnerResponseIntegrityError extends Error {
  constructor() {
    super("partner response integrity failure");
    this.name = "PartnerResponseIntegrityError";
  }
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
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}


function timestamp(value: unknown, optional = false): value is string | null {
  if (optional && value === null) return true;
  return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value));
}


const organizationKeys = [
  "partner_organization_id", "display_name", "status", "created_at", "updated_at",
  "invalidated_at",
] as const;
const operatorKeys = [
  "partner_operator_id", "subject_id", "partner_organization_id", "display_name", "status",
  "fae_grant_active", "fae_granted_at", "created_at", "updated_at", "invalidated_at",
] as const;
const bindingRequestKeys = [
  "binding_request_id", "provider_kind", "display_name", "status", "verified_at",
  "requested_at", "expires_at", "resolved_at", "linked_partner_operator_id",
] as const;


function parseOrganization(value: unknown): PartnerOrganization {
  if (!isObject(value) || !exactKeys(value, organizationKeys)
    || !uuid(value.partner_organization_id)
    || typeof value.display_name !== "string" || !value.display_name
    || !["active", "suspended", "disabled"].includes(String(value.status))
    || !timestamp(value.created_at) || !timestamp(value.updated_at)
    || !timestamp(value.invalidated_at, true)) {
    throw new PartnerResponseIntegrityError();
  }
  return value as unknown as PartnerOrganization;
}


function parseOperator(value: unknown): PartnerOperator {
  if (!isObject(value) || !exactKeys(value, operatorKeys)
    || !uuid(value.partner_operator_id) || !uuid(value.subject_id)
    || !uuid(value.partner_organization_id)
    || typeof value.display_name !== "string" || !value.display_name
    || !["active", "suspended", "disabled"].includes(String(value.status))
    || typeof value.fae_grant_active !== "boolean"
    || !timestamp(value.fae_granted_at, true)
    || !timestamp(value.created_at) || !timestamp(value.updated_at)
    || !timestamp(value.invalidated_at, true)) {
    throw new PartnerResponseIntegrityError();
  }
  return value as unknown as PartnerOperator;
}


function parseBindingRequest(value: unknown): PartnerBindingRequest {
  if (!isObject(value) || !exactKeys(value, bindingRequestKeys)
    || !uuid(value.binding_request_id)
    || typeof value.provider_kind !== "string" || !value.provider_kind
    || (value.display_name !== null
      && (typeof value.display_name !== "string" || !value.display_name))
    || !["pending", "linked", "rejected", "expired"].includes(String(value.status))
    || !timestamp(value.verified_at) || !timestamp(value.requested_at)
    || !timestamp(value.expires_at) || !timestamp(value.resolved_at, true)
    || (value.linked_partner_operator_id !== null
      && !uuid(value.linked_partner_operator_id))) {
    throw new PartnerResponseIntegrityError();
  }
  return value as unknown as PartnerBindingRequest;
}


export function parsePartnerMutationResult(value: unknown): PartnerMutationResult {
  if (!isObject(value) || !exactKeys(value, ["kind", "projection"])) {
    throw new PartnerResponseIntegrityError();
  }
  if (value.kind === "organization") {
    return { kind: value.kind, projection: parseOrganization(value.projection) };
  }
  if (value.kind === "operator") {
    return { kind: value.kind, projection: parseOperator(value.projection) };
  }
  if (value.kind === "binding_request") {
    return { kind: value.kind, projection: parseBindingRequest(value.projection) };
  }
  throw new PartnerResponseIntegrityError();
}


async function responseJson(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}


async function readCollection<T>(
  path: string,
  key: string,
  parse: (value: unknown) => T,
): Promise<T[]> {
  const response = await fetch(platformPath(path), {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  const value = await responseJson(response);
  if (!response.ok) throw new PartnerApiError(response.status, value);
  if (!isObject(value) || !exactKeys(value, [key]) || !Array.isArray(value[key])) {
    throw new PartnerResponseIntegrityError();
  }
  return value[key].map(parse);
}


async function mutate(
  path: string,
  method: "POST" | "PATCH" | "PUT" | "DELETE",
  csrfToken: string,
  requestId: string,
  body: Record<string, unknown>,
  resultKey: "organization" | "operator" | "binding_request",
): Promise<PartnerMutationResult> {
  const response = await fetch(platformPath(path), {
    method,
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({ ...body, request_id: requestId }),
  });
  const value = await responseJson(response);
  if (!response.ok) {
    if (response.status >= 500) {
      const detail = isObject(value) && isObject(value.detail) ? value.detail : null;
      if (detail?.code === "partner_mutation_indeterminate"
        && typeof detail.request_id === "string"
        && detail.request_id !== requestId) {
        throw new PartnerResponseIntegrityError();
      }
      throw new PartnerMutationIndeterminate(requestId, value);
    }
    throw new PartnerApiError(response.status, value);
  }
  const allowed = ["request_id", resultKey];
  if (!isObject(value) || !exactKeys(value, allowed)
    || typeof value.request_id !== "string" || value.request_id !== requestId) {
    throw new PartnerResponseIntegrityError();
  }
  if (resultKey === "organization") {
    return parsePartnerMutationResult({ kind: resultKey, projection: value[resultKey] });
  }
  if (resultKey === "operator") {
    return parsePartnerMutationResult({ kind: resultKey, projection: value[resultKey] });
  }
  return parsePartnerMutationResult({ kind: resultKey, projection: value[resultKey] });
}


export const partnerApi = {
  async load(): Promise<PartnerSnapshot> {
    const [organizations, operators, bindingRequests] = await Promise.all([
      readCollection(
        "/api/v1/manage/partners/organizations",
        "organizations",
        parseOrganization,
      ),
      readCollection(
        "/api/v1/manage/partners/operators",
        "operators",
        parseOperator,
      ),
      readCollection(
        "/api/v1/manage/partners/binding-requests",
        "binding_requests",
        parseBindingRequest,
      ),
    ]);
    return { organizations, operators, bindingRequests };
  },

  createOrganization(
    csrfToken: string, requestId: string, displayName: string, reason: string,
  ): Promise<PartnerMutationResult> {
    return mutate(
      "/api/v1/manage/partners/organizations", "POST", csrfToken, requestId,
      { display_name: displayName, reason }, "organization",
    );
  },

  setOrganizationStatus(
    csrfToken: string, requestId: string, organizationId: string,
    status: PartnerStatus, reason: string,
  ): Promise<PartnerMutationResult> {
    return mutate(
      `/api/v1/manage/partners/organizations/${organizationId}/status`,
      "PATCH", csrfToken, requestId, { status, reason }, "organization",
    );
  },

  createOperator(
    csrfToken: string, requestId: string, organizationId: string,
    displayName: string, reason: string,
  ): Promise<PartnerMutationResult> {
    return mutate(
      "/api/v1/manage/partners/operators", "POST", csrfToken, requestId,
      { partner_organization_id: organizationId, display_name: displayName, reason },
      "operator",
    );
  },

  setOperatorStatus(
    csrfToken: string, requestId: string, operatorId: string,
    status: PartnerStatus, reason: string,
  ): Promise<PartnerMutationResult> {
    return mutate(
      `/api/v1/manage/partners/operators/${operatorId}/status`,
      "PATCH", csrfToken, requestId, { status, reason }, "operator",
    );
  },

  setFaeGrant(
    csrfToken: string, requestId: string, operatorId: string,
    granted: boolean, reason: string,
  ): Promise<PartnerMutationResult> {
    return mutate(
      `/api/v1/manage/partners/operators/${operatorId}/fae-grant`,
      granted ? "PUT" : "DELETE", csrfToken, requestId, { reason }, "operator",
    );
  },

  linkBindingRequest(
    csrfToken: string, requestId: string, bindingRequestId: string,
    operatorId: string, reason: string,
  ): Promise<PartnerMutationResult> {
    return mutate(
      `/api/v1/manage/partners/binding-requests/${bindingRequestId}/link`,
      "POST", csrfToken, requestId,
      { partner_operator_id: operatorId, reason }, "binding_request",
    );
  },

  rejectBindingRequest(
    csrfToken: string, requestId: string, bindingRequestId: string, reason: string,
  ): Promise<PartnerMutationResult> {
    return mutate(
      `/api/v1/manage/partners/binding-requests/${bindingRequestId}/reject`,
      "POST", csrfToken, requestId, { reason }, "binding_request",
    );
  },
};


export type PartnerApi = typeof partnerApi;
