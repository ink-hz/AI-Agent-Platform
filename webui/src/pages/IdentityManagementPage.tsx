import { useEffect, useState } from "react";

import {
  DirectoryUnavailable,
  PermissionDenied,
  changeObservationScope,
  changeViewer,
  listManagedUsers,
  type Account,
  type ManagedUser,
} from "../auth";


function failureMessage(error: unknown): string {
  if (error instanceof DirectoryUnavailable || (error instanceof Error && error.message.includes("503"))) {
    return "审计或目录服务暂不可用，未执行任何变更。";
  }
  if (error instanceof PermissionDenied) return "当前账号无权管理身份。";
  if (error instanceof Error && error.message.includes("409")) return "用户状态已变化，请刷新后重试。";
  return "暂时无法读取身份数据。";
}


export function IdentityManagementPage({ account }: { account: Account }) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [scopeDrafts, setScopeDrafts] = useState<Record<string, string>>({});
  const load = async () => {
    try { setUsers(await listManagedUsers()); } catch (error) { setMessage(failureMessage(error)); }
  };
  useEffect(() => { void load(); }, []);
  if (account.role !== "platform_owner") {
    return <section className="permission-state" role="alert"><h1>无权访问</h1><p>只有平台所有者可以修改角色和观察范围。</p></section>;
  }
  const mutate = async (user: ManagedUser) => {
    if (!reason.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await changeViewer(account, user, reason.trim());
      setMessage("变更成功，服务端已记录审计事件。");
      setReason("");
      await load();
    } catch (error) {
      setMessage(failureMessage(error));
    } finally { setBusy(false); }
  };
  const mutateScope = async (user: ManagedUser, agentId: string, revoke = false) => {
    if (!reason.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await changeObservationScope(account, user, agentId, reason.trim(), revoke);
      setMessage("变更成功，服务端已记录审计事件。");
      setScopeDrafts((current) => ({ ...current, [user.internal_user_id]: "" }));
      setReason("");
      await load();
    } catch (error) {
      setMessage(failureMessage(error));
    } finally { setBusy(false); }
  };
  return (
    <section className="identity-page">
      <header><p>PLATFORM GOVERNANCE</p><h1>身份与观察范围</h1><span>角色和 Agent 范围均由后端执行；页面隐藏不是权限控制。</span></header>
      <label className="identity-reason">变更原因
        <input aria-label="变更原因" value={reason} onInput={(event) => setReason(event.currentTarget.value)} placeholder="填写审批或业务原因" />
      </label>
      {message && <p className={`auth-message ${message.startsWith("变更成功") ? "is-success" : "is-error"}`} role="status">{message}</p>}
      <div className="identity-users">
        {users.map((user) => <article key={user.internal_user_id}>
          <div><strong>{user.display_name}</strong><span>{user.status === "active" ? "在职" : "不可用"}</span></div>
          <p>{user.role === "management_viewer" ? "只读观察者" : user.role === "platform_owner" ? "平台所有者" : "企业成员"}</p>
          <small>{user.scopes.length ? `范围：${user.scopes.join("、")}` : "未授予 Agent 观察范围"}</small>
          {user.role !== "platform_owner" && <button type="button" disabled={busy || !reason.trim()} onClick={() => void mutate(user)}>
            {user.role === "management_viewer" ? "撤销只读观察者" : "设为只读观察者"}
          </button>}
          {user.role === "management_viewer" && <div className="scope-controls">
            <label>新增 Agent 范围<input
              aria-label={`${user.display_name}的新 Agent 范围`}
              value={scopeDrafts[user.internal_user_id] || ""}
              onInput={(event) => setScopeDrafts((current) => ({ ...current, [user.internal_user_id]: event.currentTarget.value }))}
              placeholder="精确 Agent ID"
            /></label>
            <button type="button" disabled={busy || !reason.trim() || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(scopeDrafts[user.internal_user_id] || "")} onClick={() => void mutateScope(user, scopeDrafts[user.internal_user_id] || "")}>授予范围</button>
            {user.scopes.map((scope) => <button className="scope-revoke" type="button" key={scope} disabled={busy || !reason.trim()} onClick={() => void mutateScope(user, scope, true)}>撤销 {scope}</button>)}
          </div>}
        </article>)}
      </div>
    </section>
  );
}
