import type { ReactNode } from "react";

import { PlatformLink } from "../PlatformLink";


const ITEMS = [
  ["概览", "/admin/fae", "overview"],
  ["Sessions", "/admin/fae/sessions", "sessions"],
  ["反馈与修复", "/admin/fae/issues", "issues"],
  ["分析报告", "/admin/fae/reports", "reports"],
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
      <nav aria-label="FAE 工作台">{ITEMS.map(([label, href, section]) =>
        <PlatformLink aria-current={currentSection === section ? "page" : undefined} href={href} key={href}>{label}</PlatformLink>,
      )}</nav>
    </aside>
    <div className="fae-workbench__content">{children}</div>
  </section>;
}
