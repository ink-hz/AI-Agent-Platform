import type { ReactNode } from "react";

import { platformPath, type Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";


export type HrWorkspaceSection = "chat" | "positions";


export function HrWorkspaceShell({
  account,
  current,
  children,
}: {
  account: Account;
  current: HrWorkspaceSection;
  children: ReactNode;
}) {
  return <section className="hr-workspace-shell">
    <header className="hr-workspace-topbar">
      <PlatformLink className="hr-workspace-brand" href="/hr/" aria-label="HR 智能工作台首页">
        <span aria-hidden="true">HR</span>
        <strong>HR 智能工作台</strong>
      </PlatformLink>
      <nav className="hr-workspace-nav" aria-label="HR 智能工作台">
        <PlatformLink aria-current={current === "chat" ? "page" : undefined} href="/hr/">对话</PlatformLink>
        <PlatformLink aria-current={current === "positions" ? "page" : undefined} href="/hr/positions">岗位</PlatformLink>
      </nav>
      <div className="hr-workspace-actions">
        <span className="hr-workspace-identity"><span aria-hidden="true">人</span><strong>{account.display_name}</strong></span>
        <a className="hr-workspace-platform-link" href={platformPath("/")}>Agent Platform</a>
      </div>
    </header>
    {account.hard_stale_read_only && <aside className="hr-workspace-stale" role="status">
      通讯录信息已超过安全时限，当前 HR 工作台为只读状态。
    </aside>}
    <div className="hr-workspace-body">{children}</div>
  </section>;
}
