import { useEffect, useState } from "react";

import {
  ManagementMutationIndeterminate,
  PlatformApiError,
  type Account,
} from "../auth";
import {
  vocAccessApi,
  type VocAccessGrant,
} from "../vocAccessApi";


export interface VocAccessApi {
  list(): Promise<VocAccessGrant[]>;
  grant(account: Account, displayName: string, requestId: string): Promise<void>;
  revoke(account: Account, grant: VocAccessGrant, requestId: string): Promise<void>;
}

type PendingMutation =
  | {
    version: 1;
    kind: "grant";
    requestId: string;
    displayName: string;
  }
  | {
    version: 1;
    kind: "revoke";
    requestId: string;
    grant: VocAccessGrant;
  };


function pendingKey(ownerId: string): string {
  return `platform.identity.pending-voc-access.v1:${ownerId}`;
}


function readPending(ownerId: string): PendingMutation | null {
  try {
    const raw = sessionStorage.getItem(pendingKey(ownerId));
    if (raw === null) return null;
    const value = JSON.parse(raw) as PendingMutation;
    if (
      value.version !== 1
      || !["grant", "revoke"].includes(value.kind)
      || typeof value.requestId !== "string"
      || !value.requestId
    ) {
      return null;
    }
    if (value.kind === "grant") {
      return typeof value.displayName === "string" && value.displayName
        ? value
        : null;
    }
    return value.grant && typeof value.grant.internal_user_id === "string"
      ? value
      : null;
  } catch {
    return null;
  }
}


function writePending(ownerId: string, pending: PendingMutation): boolean {
  try {
    const serialized = JSON.stringify(pending);
    sessionStorage.setItem(pendingKey(ownerId), serialized);
    return sessionStorage.getItem(pendingKey(ownerId)) === serialized;
  } catch {
    return false;
  }
}


function clearPending(ownerId: string): void {
  try {
    sessionStorage.removeItem(pendingKey(ownerId));
  } catch { /* An old marker is harmless after the server result is known. */ }
}


function failureMessage(error: unknown): string {
  if (error instanceof PlatformApiError && error.status === 403) {
    return "当前账号无权管理 VOC 工作台访问。";
  }
  if (error instanceof PlatformApiError && error.status === 409) {
    return "授权状态或企业目录已变化，请刷新后重试。";
  }
  if (error instanceof PlatformApiError && error.status === 422) {
    return "花名无效，未执行授权。";
  }
  return "VOC 工作台授权服务暂不可用。";
}


function statusLabel(status: string): string {
  return status === "active" ? "在职" : "不可用";
}


function OwnerVocAccessPanel({
  account,
  api,
}: {
  account: Account;
  api: VocAccessApi;
}) {
  const [grants, setGrants] = useState<VocAccessGrant[]>([]);
  const [displayName, setDisplayName] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingMutation | null>(() => (
    readPending(account.internal_user_id)
  ));

  const refresh = async () => {
    const current = await api.list();
    setGrants(current);
    return current;
  };

  useEffect(() => {
    let active = true;
    void api.list().then((current) => {
      if (active) setGrants(current);
    }).catch(() => {
      if (active) setMessage("暂时无法读取 VOC 工作台授权。请稍后重试。");
    });
    return () => { active = false; };
  }, [api]);

  const run = async (operation: PendingMutation) => {
    if (busy) return;
    if (!writePending(account.internal_user_id, operation)) {
      setMessage("无法保存待处理授权；为避免重复操作，本次未发送。请刷新后重试。");
      return;
    }
    setPending(operation);
    setBusy(true);
    setMessage("");
    try {
      if (operation.kind === "grant") {
        await api.grant(account, operation.displayName, operation.requestId);
      } else {
        await api.revoke(account, operation.grant, operation.requestId);
      }
      clearPending(account.internal_user_id);
      setPending(null);
      if (operation.kind === "grant") setDisplayName("");
      setMessage(operation.kind === "grant" ? "授权成功。" : "撤销成功。");
      try {
        await refresh();
      } catch {
        setMessage(operation.kind === "grant"
          ? "授权成功，但授权列表刷新失败。"
          : "撤销成功，但授权列表刷新失败。");
      }
    } catch (error) {
      if (
        error instanceof ManagementMutationIndeterminate
        || error instanceof TypeError
        || (error instanceof PlatformApiError && error.status >= 500)
      ) {
        setMessage("授权结果暂时无法确认，请使用同一请求重试。不得创建新请求。");
      } else {
        clearPending(account.internal_user_id);
        setPending(null);
        setMessage(failureMessage(error));
      }
    } finally {
      setBusy(false);
    }
  };

  const createGrant = () => {
    const selected = displayName.trim();
    if (!selected || pending) return;
    void run({
      version: 1,
      kind: "grant",
      requestId: crypto.randomUUID(),
      displayName: selected,
    });
  };

  const revokeGrant = (grant: VocAccessGrant) => {
    if (pending) return;
    void run({
      version: 1,
      kind: "revoke",
      requestId: crypto.randomUUID(),
      grant,
    });
  };

  return (
    <section className="identity-page" data-voc-access-panel>
      <header>
        <p>VOC ACCESS</p>
        <h2>VOC 工作台访问</h2>
        <span>按企业唯一花名为成员授权；不改变成员的 Platform 角色。</span>
      </header>
      <label className="identity-reason">花名
        <input
          aria-label="VOC 授权花名"
          value={displayName}
          maxLength={256}
          onInput={(event) => setDisplayName(event.currentTarget.value)}
          placeholder="例如：稻夫"
        />
      </label>
      <small>授权原因：VOC 工作台访问审批（服务端固定审计原因）</small>
      <div>
        <button
          type="button"
          disabled={busy || pending !== null || !displayName.trim()}
          onClick={createGrant}
        >授予 VOC 访问</button>
        {pending && (
          <button type="button" disabled={busy} onClick={() => void run(pending)}>
            使用同一请求重试
          </button>
        )}
      </div>
      {message && <p className="auth-message" role="status">{message}</p>}
      <div className="identity-users">
        {grants.map((grant) => (
          <article key={grant.grant_id}>
            <div>
              <strong>{grant.display_name}</strong>
              <span>{statusLabel(grant.status)}</span>
            </div>
            <p>VOC 工作台管理员</p>
            <small>
              创建于 {new Date(grant.created_at).toLocaleString("zh-CN")} · 行版本 {grant.row_version}
            </small>
            <button
              type="button"
              disabled={busy || pending !== null}
              onClick={() => revokeGrant(grant)}
            >撤销 VOC 访问</button>
          </article>
        ))}
      </div>
    </section>
  );
}


export function VocAccessPanel({
  account,
  api = vocAccessApi,
}: {
  account: Account;
  api?: VocAccessApi;
}) {
  if (account.role !== "platform_owner") return null;
  return <OwnerVocAccessPanel account={account} api={api} />;
}
