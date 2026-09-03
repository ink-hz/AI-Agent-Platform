/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";
import type { Account } from "./auth";
import { FaeWorkbenchShell, type FaeSection } from "./components/fae-workbench/FaeWorkbenchShell";
import type { Route } from "./router";


const member: Account = {
  internal_user_id: "member", display_name: "成员", role: "member", observation_agent_ids: [],
  departments: [], gender: null,
  directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
};


describe("usage navigation", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("gives members a use-first navigation and always sends the brand home", async () => {
    await act(async () => root.render(<AppShell route={{ name: "brain" }} account={member}><p>内容</p></AppShell>));
    expect(container.querySelector(".product-nav")?.textContent).toBe("Agent 大脑专业 Agent");
    expect(container.querySelector(".product-nav")?.textContent).not.toContain("企业账号");
    const accountEntry = container.querySelector<HTMLAnchorElement>("a.account-chip");
    expect(accountEntry?.textContent).toBe("成员");
    expect(accountEntry?.getAttribute("href")).toBe("/account");
    expect(container.querySelector<HTMLAnchorElement>(".brand")?.getAttribute("href")).toBe("/");
    expect(container.textContent).not.toContain("管理中心");
    expect(container.querySelector("main.page.is-brain-workspace")).not.toBeNull();
    expect(container.querySelector("main.page")?.className).not.toContain("is-fae-workbench");
    expect(container.querySelector(".app.is-brain-workspace-shell")).not.toBeNull();
    expect(container.querySelector("footer.site-foot")).toBeNull();
  });

  it("adds one quiet management entry for owners without replacing use navigation", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("deployment unavailable"));
    await act(async () => root.render(<AppShell
      route={{ name: "admin-overview" }} account={{ ...member, role: "platform_owner" }}
    ><p>内容</p></AppShell>));
    const navigation = container.querySelector(".product-nav")?.textContent || "";
    expect(navigation).toContain("Agent 大脑");
    expect(navigation).toContain("专业 Agent");
    expect(navigation).not.toContain("AI 工程笔记");
    expect(navigation).not.toContain("历史对话");
    expect(navigation).not.toContain("企业账号");
    expect(navigation).toContain("管理中心");
    expect(container.querySelector<HTMLAnchorElement>('a[href="/admin"]')).not.toBeNull();
    expect(container.querySelector<HTMLAnchorElement>('a[href="/admin/voc"]')?.textContent).toBe("VOC 管理");
    expect(container.querySelector('.admin-nav a[href="/admin/operations"]')).toBeNull();
    expect(container.querySelector(".admin-nav")?.textContent).not.toContain("数据飞轮");
  });

  it("adds one FAE workbench entry and keeps it selected on FAE detail routes", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("deployment unavailable"));
    await act(async () => root.render(<AppShell
      route={{ name: "admin-fae-overview" }} account={{ ...member, role: "platform_owner" }}
    ><p>内容</p></AppShell>));
    expect(container.querySelector<HTMLAnchorElement>('a[href="/admin/fae"]')?.textContent).toBe("FAE 工作台");
    expect(container.querySelectorAll('.admin-nav a[href="/admin/fae"]')).toHaveLength(1);

    window.history.replaceState({}, "", "/admin/fae/sessions/fae%3Asession-1");
    await act(async () => root.render(<AppShell
      route={{ name: "admin-fae-session", sessionKey: "fae:session-1" }} account={{ ...member, role: "platform_owner" }}
    ><p>内容</p></AppShell>));
    expect(container.querySelector<HTMLAnchorElement>('.admin-nav a[href="/admin/fae"]')?.className).toContain("is-current");
    expect(container.querySelectorAll(".admin-nav a.is-current")).toHaveLength(1);

    for (const [path, route] of [
      ["/admin/fae/issues/00000000-0000-0000-0000-000000000001", { name: "admin-fae-issue", issueId: "00000000-0000-0000-0000-000000000001" }],
      ["/admin/fae/reports/weekly:2026-08-31", { name: "admin-fae-report", reportId: "weekly:2026-08-31" }],
    ] as const) {
      window.history.replaceState({}, "", path);
      await act(async () => root.render(<AppShell route={route} account={{ ...member, role: "platform_owner" }}><p>内容</p></AppShell>));
      expect(container.querySelector<HTMLAnchorElement>('.admin-nav a[href="/admin/fae"]')?.className).toContain("is-current");
    }
  });

  it("uses one main landmark and the reachable 1440 workspace wrapper for every FAE route", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("deployment unavailable"));
    const routes: Array<[Route, FaeSection]> = [
      [{ name: "admin-fae-overview" }, "overview"],
      [{ name: "admin-fae-sessions" }, "sessions"],
      [{ name: "admin-fae-session", sessionKey: "fae:session-1" }, "sessions"],
      [{ name: "admin-fae-issues" }, "issues"],
      [{ name: "admin-fae-issue", issueId: "00000000-0000-0000-0000-000000000001" }, "issues"],
      [{ name: "admin-fae-reports" }, "reports"],
      [{ name: "admin-fae-report", reportId: "weekly:2026-08-31" }, "reports"],
    ];

    for (const [route, section] of routes) {
      await act(async () => root.render(<AppShell route={route} account={{ ...member, role: "platform_owner" }}>
        <FaeWorkbenchShell currentSection={section}><p>FAE route content</p></FaeWorkbenchShell>
      </AppShell>));
      expect(container.querySelectorAll("main")).toHaveLength(1);
      expect(container.querySelector("main.page")?.className).toContain("is-fae-workbench");
      expect(container.querySelector(".fae-workbench__content")?.textContent).toContain("FAE route content");
    }
  });

  it("gives management viewers a direct VOC management entry only", async () => {
    await act(async () => root.render(<AppShell
      route={{ name: "admin-voc" }} account={{ ...member, role: "management_viewer" }}
    ><p>VOC 内容</p></AppShell>));

    const product = container.querySelector(".product-nav")?.textContent || "";
    expect(product).toContain("管理中心");
    expect(container.querySelector<HTMLAnchorElement>('.product-nav a[href="/admin/voc"]')).not.toBeNull();
    expect(container.querySelector(".admin-nav")?.textContent).toBe("VOC 管理");
    expect(container.querySelector<HTMLAnchorElement>('.admin-nav a[href="/admin/voc"]')?.getAttribute("aria-current")).toBeNull();
  });

  it("keeps the AI notes workspace without a global navigation entry", async () => {
    await act(async () => root.render(
      <AppShell route={{ name: "ai-notes" }} account={member}><p>文章</p></AppShell>,
    ));
    expect(container.querySelector('.product-nav a[href="/ai-notes"]')).toBeNull();
    expect(container.querySelector("main.page.is-ai-notes-workspace")).not.toBeNull();
    expect(container.querySelector(".app.is-ai-notes-workspace-shell")).not.toBeNull();
    expect(container.querySelector("footer.site-foot")).toBeNull();
  });

  it("marks the employee name as the current account entry on the account page", async () => {
    await act(async () => root.render(<AppShell route={{ name: "account" }} account={member}><p>账号信息</p></AppShell>));
    const accountEntry = container.querySelector<HTMLAnchorElement>("a.account-chip");
    expect(accountEntry?.getAttribute("href")).toBe("/account");
    expect(accountEntry?.getAttribute("aria-current")).toBe("page");
  });

  it("uses the same workspace shell for a selected conversation", async () => {
    await act(async () => root.render(<AppShell
      route={{ name: "conversation", conversationId: "conversation-1" }} account={member}
    ><p>持续对话</p></AppShell>));
    expect(container.querySelector("main.page.is-brain-workspace")).not.toBeNull();
    expect(container.querySelector('a[aria-current="page"]')?.textContent).toBe("Agent 大脑");
    expect(container.querySelector("footer.site-foot")).toBeNull();
  });

  it("uses the conversation workspace shell for canonical HR and Marketing routes", async () => {
    const routes: Route[] = [
      { name: "hr" },
      { name: "hr-conversation", conversationId: "c-1" },
      { name: "marketing", agentSlug: "inbound" },
      { name: "marketing-conversation", agentSlug: "voice", conversationId: "c-2" },
    ];

    for (const route of routes) {
      await act(async () => root.render(<AppShell route={route} account={member}><p>专业工作区</p></AppShell>));
      expect(container.querySelector("main.page.is-brain-workspace")).not.toBeNull();
      expect(container.querySelector(".app.is-brain-workspace-shell")).not.toBeNull();
      expect(container.querySelector("footer.site-foot")).toBeNull();
    }
  });
});
