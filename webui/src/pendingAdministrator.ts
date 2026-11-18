import { isUuid, type AdministratorMutation } from "./auth";


const STORAGE_PREFIX = "platform.identity.pending-administrator.v1";

export type PendingAdministratorState =
  | { kind: "none" }
  | { kind: "inflight_no_replay"; operation: AdministratorMutation }
  | { kind: "pending_replay"; operation: AdministratorMutation }
  | { kind: "confirmed_needs_refresh"; operation: AdministratorMutation }
  | { kind: "integrity_failure" };

function storageKey(ownerInternalUserId: string): string {
  return `${STORAGE_PREFIX}:${encodeURIComponent(ownerInternalUserId)}`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  const keys = Object.keys(value).sort();
  return keys.length === expected.length
    && keys.every((key, index) => key === [...expected].sort()[index]);
}

function integrityRecord(): string {
  return JSON.stringify({ version: 1, kind: "integrity_failure" });
}

export function storeAdministratorIntegrityFailure(ownerInternalUserId: string): boolean {
  try {
    sessionStorage.setItem(storageKey(ownerInternalUserId), integrityRecord());
    return true;
  } catch {
    return false;
  }
}

export function loadPendingAdministrator(ownerInternalUserId: string): PendingAdministratorState {
  let raw: string | null;
  try { raw = sessionStorage.getItem(storageKey(ownerInternalUserId)); } catch { return { kind: "integrity_failure" }; }
  if (raw === null) return { kind: "none" };
  try {
    const value: unknown = JSON.parse(raw);
    if (!isObject(value) || value.version !== 1 || typeof value.kind !== "string") throw new Error("invalid pending administrator state");
    if (value.kind === "integrity_failure" && hasExactKeys(value, ["version", "kind"])) {
      return { kind: "integrity_failure" };
    }
    const keys = ["version", "kind", "target_internal_user_id", "action", "request_id"];
    let roster: { directoryMemberId: string; directoryGenerationId: string } | undefined;
    if ("directory_member_id" in value || "directory_generation_id" in value) {
      if (value.action !== "assign" || !isUuid(value.directory_member_id) || !isUuid(value.directory_generation_id)) {
        throw new Error("invalid pending roster assignment");
      }
      keys.push("directory_member_id", "directory_generation_id");
      roster = { directoryMemberId: value.directory_member_id, directoryGenerationId: value.directory_generation_id };
    }
    if (
      (value.kind === "inflight_no_replay"
        || value.kind === "pending_replay"
        || value.kind === "confirmed_needs_refresh")
      && hasExactKeys(value, keys)
      && isUuid(value.target_internal_user_id)
      && (value.action === "assign" || value.action === "revoke")
      && isUuid(value.request_id)
    ) {
      return {
        kind: value.kind,
        operation: Object.freeze<AdministratorMutation>({
          targetInternalUserId: value.target_internal_user_id,
          ...(roster ? { revoke: false, ...roster } : { revoke: value.action === "revoke" }),
          requestId: value.request_id,
        }),
      };
    }
  } catch { /* Canonicalize all corrupt and unknown records below. */ }
  storeAdministratorIntegrityFailure(ownerInternalUserId);
  return { kind: "integrity_failure" };
}

export function storeInflightAdministrator(
  ownerInternalUserId: string,
  operation: AdministratorMutation,
): boolean {
  return storeAdministratorOperation(ownerInternalUserId, "inflight_no_replay", operation);
}

export function storePendingAdministratorReplay(
  ownerInternalUserId: string,
  operation: AdministratorMutation,
): boolean {
  return storeAdministratorOperation(ownerInternalUserId, "pending_replay", operation);
}

export function storeConfirmedAdministratorRefresh(
  ownerInternalUserId: string,
  operation: AdministratorMutation,
): boolean {
  return storeAdministratorOperation(
    ownerInternalUserId, "confirmed_needs_refresh", operation,
  );
}

function storeAdministratorOperation(
  ownerInternalUserId: string,
  kind: "inflight_no_replay" | "pending_replay" | "confirmed_needs_refresh",
  operation: AdministratorMutation,
): boolean {
  try {
    sessionStorage.setItem(storageKey(ownerInternalUserId), JSON.stringify({
      version: 1,
      kind,
      target_internal_user_id: operation.targetInternalUserId,
      directory_member_id: operation.directoryMemberId,
      directory_generation_id: operation.directoryGenerationId,
      action: operation.revoke ? "revoke" : "assign",
      request_id: operation.requestId,
    }));
    return true;
  } catch {
    return false;
  }
}

export function clearPendingAdministrator(ownerInternalUserId: string): boolean {
  try {
    sessionStorage.removeItem(storageKey(ownerInternalUserId));
    return true;
  } catch {
    return false;
  }
}
