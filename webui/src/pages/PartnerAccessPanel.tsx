import { useEffect, useState } from "react";

import type { Account } from "../auth";
import {
  PartnerApiError,
  PartnerMutationIndeterminate,
  PartnerResponseIntegrityError,
  parsePartnerMutationResult,
  partnerApi,
  type PartnerApi,
  type PartnerBindingRequest,
  type PartnerMutationResult,
  type PartnerOperator,
  type PartnerOrganization,
  type PartnerSnapshot,
  type PartnerStatus,
} from "../partnerApi";


type PendingKind = "inflight" | "confirmed" | "indeterminate" | "integrity_failure";
interface PendingPartnerBase {
  version: 1;
  request_id: string;
  label: string;
}
type PendingPartnerOperation = PendingPartnerBase & (
  | { kind: "confirmed"; expected: PartnerMutationResult }
  | {
    kind: Exclude<PendingKind, "confirmed">;
    expected: null;
  }
);
type PendingPartnerMutation = PendingPartnerOperation | {
  version: 1;
  kind: "integrity_failure";
};


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;


function pendingKey(ownerId: string): string {
  return `platform.identity.pending-partner.v1:${ownerId}`;
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


function exactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const selected = [...expected].sort();
  return actual.length === selected.length
    && actual.every((key, index) => key === selected[index]);
}


function canonicalIntegrityFailure(ownerId: string): PendingPartnerMutation {
  const failure = { version: 1, kind: "integrity_failure" } as const;
  try {
    sessionStorage.setItem(pendingKey(ownerId), JSON.stringify(failure));
  } catch { /* The in-memory failure still keeps this mount blocked. */ }
  return failure;
}


function readPending(ownerId: string): PendingPartnerMutation | null {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(pendingKey(ownerId));
  } catch {
    return canonicalIntegrityFailure(ownerId);
  }
  if (raw === null) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!isObject(value) || value.version !== 1 || typeof value.kind !== "string") {
      throw new Error("invalid pending partner state");
    }
    if (value.kind === "integrity_failure" && exactKeys(value, ["version", "kind"])) {
      return { version: 1, kind: value.kind };
    }
    if (!exactKeys(
      value, ["version", "kind", "request_id", "label", "expected"],
    ) || !["inflight", "confirmed", "indeterminate", "integrity_failure"].includes(
      value.kind,
    ) || typeof value.request_id !== "string" || !UUID.test(value.request_id)
      || typeof value.label !== "string" || !value.label
      || value.label !== value.label.trim() || value.label.length > 512
      || value.label.includes("\0")) {
      throw new Error("invalid pending partner operation");
    }
    const kind = value.kind as PendingKind;
    let expected: PartnerMutationResult | null = null;
    if (value.expected !== null) expected = parsePartnerMutationResult(value.expected);
    if ((kind === "confirmed") !== (expected !== null)) {
      throw new Error("invalid pending partner expectation");
    }
    const operation = {
      version: 1 as const,
      request_id: value.request_id,
      label: value.label,
    };
    if (kind === "confirmed" && expected !== null) {
      return { ...operation, kind, expected };
    }
    return {
      ...operation,
      kind: kind as Exclude<PendingKind, "confirmed">,
      expected: null,
    };
  } catch {
    return canonicalIntegrityFailure(ownerId);
  }
}


function writePending(ownerId: string, pending: PendingPartnerMutation): boolean {
  try {
    sessionStorage.setItem(pendingKey(ownerId), JSON.stringify(pending));
    return sessionStorage.getItem(pendingKey(ownerId)) === JSON.stringify(pending);
  } catch {
    return false;
  }
}


function clearPending(ownerId: string): boolean {
  try {
    sessionStorage.removeItem(pendingKey(ownerId));
    return sessionStorage.getItem(pendingKey(ownerId)) === null;
  } catch {
    return false;
  }
}


function statusLabel(status: PartnerStatus): string {
  if (status === "active") return "启用";
  if (status === "suspended") return "暂停";
  return "停用";
}


function failureMessage(error: unknown): string {
  if (error instanceof PartnerApiError && error.status === 403) {
    return "当前账号无权管理合作方访问。";
  }
  if (error instanceof PartnerApiError && error.status === 409) {
    return "合作方状态已变化，请刷新后重新操作。";
  }
  return "合作方访问变更失败，未确认任何成功状态。";
}


function sameProjection(left: object, right: object): boolean {
  const selectedLeft = left as Record<string, unknown>;
  const selectedRight = right as Record<string, unknown>;
  return exactKeys(selectedLeft, Object.keys(selectedRight))
    && Object.entries(selectedRight).every(([key, value]) => selectedLeft[key] === value);
}


function snapshotMatches(
  snapshot: PartnerSnapshot,
  expected: PartnerMutationResult,
): boolean {
  if (expected.kind === "organization") {
    const actual = snapshot.organizations.find((item) => (
      item.partner_organization_id === expected.projection.partner_organization_id
    ));
    return actual !== undefined && sameProjection(actual, expected.projection);
  }
  if (expected.kind === "operator") {
    const actual = snapshot.operators.find((item) => (
      item.partner_operator_id === expected.projection.partner_operator_id
    ));
    return actual !== undefined && sameProjection(actual, expected.projection);
  }
  const actual = snapshot.bindingRequests.find((item) => (
    item.binding_request_id === expected.projection.binding_request_id
  ));
  return actual !== undefined && sameProjection(actual, expected.projection);
}


function OwnerPartnerAccessPanel({
  account,
  api,
}: {
  account: Account;
  api: PartnerApi;
}) {
  const [organizations, setOrganizations] = useState<PartnerOrganization[]>([]);
  const [operators, setOperators] = useState<PartnerOperator[]>([]);
  const [bindingRequests, setBindingRequests] = useState<PartnerBindingRequest[]>([]);
  const [reason, setReason] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [operatorName, setOperatorName] = useState("");
  const [operatorOrganizationId, setOperatorOrganizationId] = useState("");
  const [linkTargets, setLinkTargets] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingPartnerMutation | null>(() => (
    readPending(account.internal_user_id)
  ));
  const blocked = pending !== null;
  const validReason = reason.trim().length >= 3 && reason.trim().length <= 500;

  const applySnapshot = (snapshot: PartnerSnapshot) => {
    setOrganizations(snapshot.organizations);
    setOperators(snapshot.operators);
    setBindingRequests(snapshot.bindingRequests);
    setOperatorOrganizationId((current) => {
      const active = snapshot.organizations
        .filter((item) => item.status === "active")
        .map((item) => item.partner_organization_id);
      return active.includes(current) ? current : active[0] || "";
    });
    setLinkTargets((current) => {
      const active = new Set(snapshot.operators
        .filter((item) => item.status === "active")
        .map((item) => item.partner_operator_id));
      const next: Record<string, string> = {};
      for (const request of snapshot.bindingRequests) {
        const selected = current[request.binding_request_id] || "";
        next[request.binding_request_id] = active.has(selected) ? selected : "";
      }
      return next;
    });
  };

  const refresh = async () => {
    const snapshot = await api.load();
    applySnapshot(snapshot);
    return snapshot;
  };

  useEffect(() => {
    let active = true;
    void api.load().then((snapshot) => {
      if (!active) return;
      applySnapshot(snapshot);
      if (pending?.kind === "confirmed") {
        if (!snapshotMatches(snapshot, pending.expected)) {
          setMessage("服务端已确认变更，但刷新状态未能与响应一致；请人工核查。");
        } else if (clearPending(account.internal_user_id)) {
          setPending(null);
          setMessage("变更成功，服务端已确认并刷新合作方状态。");
        } else {
          setMessage("变更已确认，但本地待处理状态无法清除；请手动核查。");
        }
      } else if (pending?.kind === "indeterminate" || pending?.kind === "inflight") {
        setMessage("合作方变更结果无法确认；当前状态仅供参考，请人工核查治理审计。");
      } else if (pending?.kind === "integrity_failure") {
        setMessage("合作方变更响应校验失败；已停止新的合作方变更，请人工核查。");
      }
    }).catch(() => {
      if (!active) return;
      setMessage(pending
        ? "合作方变更结果无法确认，且当前状态刷新失败；请人工核查治理审计。"
        : "无法读取合作方访问状态，请稍后重试。");
    });
    return () => { active = false; };
  }, []);

  const retain = (next: PendingPartnerMutation): boolean => {
    if (!writePending(account.internal_user_id, next)) {
      setPending({ version: 1, kind: "integrity_failure" });
      setMessage("无法保存合作方待处理操作；已停止新的合作方变更，请人工核查。");
      return false;
    }
    setPending(next);
    return true;
  };

  const runMutation = async (
    label: string,
    operation: (
      requestId: string,
      selectedReason: string,
    ) => Promise<PartnerMutationResult>,
  ) => {
    const selectedReason = reason.trim();
    if (blocked || busy || selectedReason.length < 3 || selectedReason.length > 500) return;
    const requestId = crypto.randomUUID();
    const inflight = {
      version: 1 as const,
      kind: "inflight" as const,
      request_id: requestId,
      label,
      expected: null,
    };
    if (!retain(inflight)) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await operation(requestId, selectedReason);
      const confirmed: PendingPartnerOperation = {
        ...inflight,
        kind: "confirmed",
        expected: result,
      };
      if (!retain(confirmed)) return;
      let snapshot: PartnerSnapshot;
      try {
        snapshot = await refresh();
      } catch {
        setMessage("合作方变更已由服务端确认，但当前状态刷新失败。");
        return;
      }
      if (!snapshotMatches(snapshot, result)) {
        setMessage("服务端已确认变更，但刷新状态未能与响应一致；请人工核查。");
        return;
      }
      if (!clearPending(account.internal_user_id)) {
        setMessage("合作方变更已确认，但本地待处理状态无法清除；请手动核查。");
        return;
      }
      setPending(null);
      setReason("");
      setMessage("变更成功，服务端已确认并刷新合作方状态。");
    } catch (error) {
      if (error instanceof PartnerResponseIntegrityError) {
        retain({ ...inflight, kind: "integrity_failure" });
        setMessage("合作方变更响应校验失败；已停止新的合作方变更，请人工核查。");
      } else if (error instanceof PartnerMutationIndeterminate
        || error instanceof TypeError
        || (error instanceof PartnerApiError && error.status >= 500)) {
        retain({ ...inflight, kind: "indeterminate" });
        setMessage("合作方变更结果无法确认；未显示成功，请人工核查治理审计。");
      } else if (error instanceof PartnerApiError && error.status >= 400 && error.status < 500) {
        if (clearPending(account.internal_user_id)) setPending(null);
        setMessage(failureMessage(error));
      } else {
        retain({ ...inflight, kind: "integrity_failure" });
        setMessage("合作方变更遇到无法分类的错误；操作不可重放，请人工核查。");
      }
    } finally {
      setBusy(false);
    }
  };

  const mutationDisabled = busy || blocked || !validReason;
  const pendingRequests = bindingRequests.filter((item) => item.status === "pending");

  return (
    <section className="identity-page" data-partner-access-panel>
      <header>
        <p>PARTNER ACCESS</p>
        <h2>合作方客服</h2>
        <span>仅平台所有者可管理；Provider 原始身份和凭据不会显示在此页面。</span>
      </header>
      <label className="identity-reason">合作方变更原因
        <input
          aria-label="合作方变更原因"
          value={reason}
          maxLength={500}
          onInput={(event) => setReason(event.currentTarget.value)}
          placeholder="至少 3 个字符"
        />
      </label>
      {message && <p className={message.startsWith("变更成功") ? "auth-message is-success" : "auth-message is-error"} role="status">{message}</p>}
      {blocked && <button type="button" disabled={busy} onClick={() => {
        setBusy(true);
        void refresh().then((snapshot) => {
          if (pending?.kind !== "confirmed") {
            setMessage("合作方变更结果仍无法确认；已刷新当前状态，请人工核查治理审计。");
            return;
          }
          if (!snapshotMatches(snapshot, pending.expected)) {
            setMessage("服务端已确认变更，但刷新状态未能与响应一致；请人工核查。");
            return;
          }
          if (!clearPending(account.internal_user_id)) {
            setMessage("变更已确认，但本地待处理状态无法清除；请手动核查。");
            return;
          }
          setPending(null);
          setReason("");
          setMessage("变更成功，服务端已确认并刷新合作方状态。");
        }).catch(() => {
          setMessage("合作方变更结果无法确认，且当前状态刷新失败；请人工核查治理审计。");
        }).finally(() => setBusy(false));
      }}>刷新合作方状态</button>}

      <div className="scope-controls">
        <label>合作方名称
          <input aria-label="合作方名称" value={organizationName} onInput={(event) => setOrganizationName(event.currentTarget.value)} />
        </label>
        <button type="button" disabled={mutationDisabled || !organizationName.trim()} onClick={() => void runMutation(
          "创建合作方",
          (requestId, selectedReason) => api.createOrganization(
            account.csrf_token, requestId, organizationName.trim(), selectedReason,
          ),
        )}>创建合作方</button>
      </div>

      <div className="identity-users">
        {organizations.map((organization) => <article key={organization.partner_organization_id}>
          <div><strong>{organization.display_name}</strong><span>{statusLabel(organization.status)}</span></div>
          <small>内部组织 ID：{organization.partner_organization_id}</small>
          {(["active", "suspended", "disabled"] as PartnerStatus[])
            .filter((status) => status !== organization.status)
            .map((status) => <button type="button" key={status} disabled={mutationDisabled} onClick={() => void runMutation(
              `${organization.display_name}-${status}`,
              (requestId, selectedReason) => api.setOrganizationStatus(
                account.csrf_token, requestId, organization.partner_organization_id,
                status, selectedReason,
              ),
            )}>{status === "active" ? "重新启用合作方" : status === "suspended" ? "暂停合作方" : "停用合作方"}</button>)}
        </article>)}
      </div>

      <div className="scope-controls">
        <label>所属合作方
          <select aria-label="坐席所属合作方" value={operatorOrganizationId} onChange={(event) => setOperatorOrganizationId(event.currentTarget.value)}>
            {organizations.filter((item) => item.status === "active").map((item) => <option key={item.partner_organization_id} value={item.partner_organization_id}>{item.display_name}</option>)}
          </select>
        </label>
        <label>坐席展示名
          <input aria-label="坐席展示名" value={operatorName} onInput={(event) => setOperatorName(event.currentTarget.value)} />
        </label>
        <button type="button" disabled={mutationDisabled || !operatorName.trim() || !operatorOrganizationId} onClick={() => void runMutation(
          "创建合作方坐席",
          (requestId, selectedReason) => api.createOperator(
            account.csrf_token, requestId, operatorOrganizationId,
            operatorName.trim(), selectedReason,
          ),
        )}>创建坐席</button>
      </div>

      <div className="identity-users">
        {operators.map((operator) => <article key={operator.partner_operator_id}>
          <div><strong>{operator.display_name}</strong><span>{statusLabel(operator.status)}</span></div>
          <p>{operator.fae_grant_active ? "已授予 FAE" : "未授予 FAE"}</p>
          <small>内部坐席 ID：{operator.partner_operator_id}</small>
          <button type="button" disabled={mutationDisabled || (
            !operator.fae_grant_active && operator.status !== "active"
          )} onClick={() => void runMutation(
            operator.fae_grant_active ? "撤销 FAE" : "授予 FAE",
            (requestId, selectedReason) => api.setFaeGrant(
              account.csrf_token, requestId, operator.partner_operator_id,
              !operator.fae_grant_active, selectedReason,
            ),
          )}>{operator.fae_grant_active ? "撤销 FAE" : "授予 FAE"}</button>
          {(["active", "suspended", "disabled"] as PartnerStatus[])
            .filter((status) => status !== operator.status)
            .map((status) => <button type="button" key={status} disabled={mutationDisabled} onClick={() => void runMutation(
              `${operator.display_name}-${status}`,
              (requestId, selectedReason) => api.setOperatorStatus(
                account.csrf_token, requestId, operator.partner_operator_id,
                status, selectedReason,
              ),
            )}>{status === "active" ? "重新启用坐席" : status === "suspended" ? "暂停坐席" : "停用坐席"}</button>)}
        </article>)}
      </div>

      <div className="identity-users">
        {pendingRequests.map((request) => <article key={request.binding_request_id}>
          <div><strong>{request.display_name || "未提供展示名"}</strong><span>{request.provider_kind}</span></div>
          <p>待绑定请求：{request.binding_request_id}</p>
          <p>状态：{request.status}</p>
          <small>核验时间：{request.verified_at}</small>
          <small>请求时间：{request.requested_at}</small>
          <small>到期时间：{request.expires_at}</small>
          <label>绑定到坐席
            <select aria-label={`${request.binding_request_id}绑定坐席`} value={linkTargets[request.binding_request_id] || ""} onChange={(event) => {
              const selected = event.currentTarget.value;
              setLinkTargets((current) => ({
                ...current,
                [request.binding_request_id]: selected,
              }));
            }}>
              <option value="">请选择坐席</option>
              {operators.filter((item) => item.status === "active").map((item) => <option key={item.partner_operator_id} value={item.partner_operator_id}>{item.display_name}</option>)}
            </select>
          </label>
          <button type="button" disabled={mutationDisabled || !linkTargets[request.binding_request_id]} onClick={() => void runMutation(
            "绑定合作方身份",
            (requestId, selectedReason) => api.linkBindingRequest(
              account.csrf_token, requestId, request.binding_request_id,
              linkTargets[request.binding_request_id], selectedReason,
            ),
          )}>绑定到坐席</button>
          <button type="button" disabled={mutationDisabled} onClick={() => void runMutation(
            "拒绝合作方身份",
            (requestId, selectedReason) => api.rejectBindingRequest(
              account.csrf_token, requestId, request.binding_request_id, selectedReason,
            ),
          )}>拒绝请求</button>
        </article>)}
      </div>
    </section>
  );
}


export function PartnerAccessPanel({
  account,
  api = partnerApi,
}: {
  account: Account;
  api?: PartnerApi;
}) {
  if (account.role !== "platform_owner") return null;
  return <OwnerPartnerAccessPanel account={account} api={api} />;
}
