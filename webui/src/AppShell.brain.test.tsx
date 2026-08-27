/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";
import type { Account } from "./auth";


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
    expect(container.querySelector(".product-nav")?.textContent).toBe("Agent 大脑专业 AgentAI 工程笔记");
    expect(container.querySelector(".product-nav")?.textContent).not.toContain("企业账号");
    const accountEntry = container.querySelector<HTMLAnchorElement>("a.account-chip");
    expect(accountEntry?.textContent).toBe("成员");
    expect(accountEntry?.getAttribute("href")).toBe("/account");
    expect(container.querySelector<HTMLAnchorElement>(".brand")?.getAttribute("href")).toBe("/");
    expect(container.textContent).not.toContain("管理中心");
    expect(container.querySelector("main.page.is-brain-workspace")).not.toBeNull();
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
    expect(navigation).toContain("AI 工程笔记");
    expect(navigation).not.toContain("历史对话");
    expect(navigation).not.toContain("企业账号");
    expect(navigation).toContain("管理中心");
    expect(container.querySelector<HTMLAnchorElement>('a[href="/admin"]')).not.toBeNull();
  });

  it("marks AI notes as the current top-level entry", async () => {
    await act(async () => root.render(
      <AppShell route={{ name: "ai-notes" }} account={member}><p>文章</p></AppShell>,
    ));
    const current = container.querySelector<HTMLAnchorElement>('a[href="/ai-notes"]');
    expect(current?.getAttribute("aria-current")).toBe("page");
    expect(current?.textContent).toBe("AI 工程笔记");
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
});
