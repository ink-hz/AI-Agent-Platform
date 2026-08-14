import { useState } from "react";

import type { Account } from "../auth";


const ROLE_LABEL = {
  member: "企业成员",
  management_viewer: "只读观察者",
  platform_owner: "平台所有者",
} as const;


export function AccountPage({
  account,
  onLogout,
}: {
  account: Account;
  onLogout: (csrfToken: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const logout = async () => {
    setBusy(true);
    setFailed(false);
    try { await onLogout(account.csrf_token); } catch { setBusy(false); setFailed(true); }
  };
  return (
    <section className="account-page">
      <header>
        <p>企业账号</p>
        <h1>{account.display_name}</h1>
        <span>身份由钉钉组织验证，平台仅保存内部用户映射。</span>
      </header>
      <div className="account-card">
        <dl>
          <div><dt>平台角色</dt><dd>{ROLE_LABEL[account.role]}</dd></div>
          <div><dt>通讯录状态</dt><dd>{account.directory_freshness === "fresh" ? "已同步" : account.directory_freshness === "warning" ? "同步延迟" : "只读保护"}</dd></div>
          <div><dt>观察范围</dt><dd>{account.observation_agent_ids.length ? account.observation_agent_ids.join("、") : "无额外观察范围"}</dd></div>
        </dl>
        {failed && <p role="alert" className="auth-message is-error">退出失败，请重试。</p>}
        <button type="button" disabled={busy} onClick={() => void logout()}>{busy ? "正在退出…" : "退出登录"}</button>
      </div>
    </section>
  );
}
