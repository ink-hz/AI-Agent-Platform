import type { ReactNode } from "react";

import { platformPath } from "../../auth";
import { FAE_DIRECT_PATH, FAE_MANAGEMENT_PATH } from "../../platform/workspaces";
import { PlatformLink } from "../PlatformLink";


const ITEMS = [
  ["概览", `${FAE_MANAGEMENT_PATH}/`, "overview"],
  ["Sessions", `${FAE_MANAGEMENT_PATH}/sessions`, "sessions"],
  ["反馈与修复", `${FAE_MANAGEMENT_PATH}/issues`, "issues"],
  ["分析报告", `${FAE_MANAGEMENT_PATH}/reports`, "reports"],
] as const;

export type FaeSection = "overview" | "sessions" | "issues" | "reports";

interface Props {
  currentSection: FaeSection;
  children: ReactNode;
}

export function FaeWorkbenchShell({ currentSection, children }: Props) {
  return <section className="fae-workbench">
    <aside className="fae-workbench__sidebar">
      <div><p>AI FAE OPERATIONS</p><h1>FAE 工作台</h1></div>
      <nav aria-label="FAE 工作区" className="fae-workbench__workspace-nav">
        <a href={platformPath(FAE_DIRECT_PATH)}>返回 FAE Agent</a>
        <PlatformLink aria-current="page" href={`${FAE_MANAGEMENT_PATH}/`}>管理</PlatformLink>
      </nav>
      <nav aria-label="FAE 管理" className="fae-workbench__sections">{ITEMS.map(([label, href, section]) =>
        <PlatformLink aria-current={currentSection === section ? "page" : undefined} href={href} key={href}>{label}</PlatformLink>,
      )}</nav>
    </aside>
    <div className="fae-workbench__content">{children}</div>
  </section>;
}
