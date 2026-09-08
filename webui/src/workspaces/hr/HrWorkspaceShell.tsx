import type { ReactNode } from "react";
import { ArrowUpRight, BookOpen, BriefcaseBusiness, ChartNoAxesCombined, MessageSquare } from "lucide-react";

import { platformPath, type Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";


export type HrWorkspaceSection = "chat" | "positions" | "panorama";


export function HrWorkspaceShell({
  account,
  chatHref = "/hr/",
  current,
  children,
  onOpenKnowledge,
}: {
  account: Account;
  chatHref?: string;
  current: HrWorkspaceSection;
  children: ReactNode;
  onOpenKnowledge?: () => void;
}) {
  return <section className="hr-workspace-shell">
    <header className="hr-workspace-topbar">
      <PlatformLink className="hr-workspace-brand" href="/hr/" aria-label="HR 智能工作台首页">
        <span aria-hidden="true">HR</span>
        <strong>HR 智能工作台</strong>
      </PlatformLink>
      <nav className="hr-workspace-nav" aria-label="HR 智能工作台">
        <PlatformLink aria-current={current === "chat" ? "page" : undefined} href={chatHref}><MessageSquare size={17} aria-hidden="true" />对话</PlatformLink>
        <PlatformLink aria-current={current === "positions" ? "page" : undefined} href="/hr/positions"><BriefcaseBusiness size={17} aria-hidden="true" />岗位</PlatformLink>
        <PlatformLink aria-current={current === "panorama" ? "page" : undefined} href="/hr/panorama"><ChartNoAxesCombined size={17} aria-hidden="true" />HR 情报</PlatformLink>
        <button onClick={onOpenKnowledge} type="button"><BookOpen size={17} aria-hidden="true" />方法与模型</button>
      </nav>
      <div className="hr-workspace-actions">
        <span className="hr-workspace-identity"><span aria-hidden="true">{account.display_name.slice(0, 1)}</span><strong>{account.display_name}</strong></span>
        <a className="hr-workspace-platform-link" href={platformPath("/")}>Agent Platform<ArrowUpRight size={14} aria-hidden="true" /></a>
      </div>
    </header>
    {account.hard_stale_read_only && <aside className="hr-workspace-stale" role="status">
      通讯录信息已超过安全时限，当前 HR 工作台为只读状态。
    </aside>}
    <div className="hr-workspace-body">{children}</div>
  </section>;
}
