import { useEffect, useState } from "react";
import { changeObservationScope, changeViewer, listManagedUsers, type Account, type ManagedUser } from "../auth";

export function ObserverAccessPage({ account }: { account: Account }) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [query, setQuery] = useState("");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [scopeDrafts, setScopeDrafts] = useState<Record<string, string>>({});
  async function load() { setUsers(await listManagedUsers()); }
  useEffect(() => {
    let active = true;
    void listManagedUsers().then(value => { if (active) setUsers(value); })
      .catch(() => { if (active) setMessage("暂时无法读取观察者权限。"); });
    return () => { active = false; };
  }, []);
  async function mutate(user: ManagedUser, scope?: string, revoke = false) {
    if (busy || !reason.trim() || account.hard_stale_read_only) return;
    setBusy(true); setMessage("");
    try {
      if (scope !== undefined) await changeObservationScope(account, user, scope, reason.trim(), revoke);
      else await changeViewer(account, user, reason.trim());
      setReason(""); setScopeDrafts({});
      setMessage("变更成功。");
      try { await load(); } catch { setMessage("变更成功，列表刷新失败，请重新打开页面核对。"); }
    } catch { setMessage("未能确认变更结果，请刷新并核查审计记录。"); }
    finally { setBusy(false); }
  }
  const blocked = busy || !reason.trim() || account.hard_stale_read_only;
  const shown = users.filter(user => ["member", "management_viewer"].includes(user.role)
    && (query.trim() ? user.display_name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()) : user.role === "management_viewer"));
  return <section className="identity-page">
    <h2>观察者权限</h2>
    <label className="identity-reason">搜索成员<input aria-label="搜索观察者成员" value={query} onInput={event => setQuery(event.currentTarget.value)} /></label>
    <label className="identity-reason">变更原因<input aria-label="变更原因" value={reason} onInput={event => setReason(event.currentTarget.value)} /></label>
    {message && <p role="status">{message}</p>}
    <div className="identity-users">{shown.map(user => <article key={user.internal_user_id}>
      <strong>{user.display_name}</strong>
      <button type="button" disabled={blocked || (user.role === "member" && user.status !== "active")} onClick={() => void mutate(user)}>{user.role === "management_viewer" ? "撤销只读观察者" : "设为只读观察者"}</button>
      {user.role === "management_viewer" && <div className="scope-controls">
        <label>新增 Agent 范围<input aria-label={`${user.display_name}的新 Agent 范围`} value={scopeDrafts[user.internal_user_id] || ""}
          onInput={event => setScopeDrafts(current => ({ ...current, [user.internal_user_id]: event.currentTarget.value }))} placeholder="精确 Agent ID" /></label>
        <button type="button" disabled={blocked || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(scopeDrafts[user.internal_user_id] || "")} onClick={() => void mutate(user, scopeDrafts[user.internal_user_id])}>授予范围</button>
        {user.scopes.map(scope => <button type="button" key={scope} disabled={blocked} onClick={() => void mutate(user, scope, true)}>撤销 {scope}</button>)}
      </div>}
    </article>)}</div>
  </section>;
}
