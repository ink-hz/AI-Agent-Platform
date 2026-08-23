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
    expect(container.querySelector(".product-nav")?.textContent).toBe("Agent 大脑专业 Agent历史任务企业账号");
    expect(container.querySelector<HTMLAnchorElement>(".brand")?.getAttribute("href")).toBe("/");
    expect(container.textContent).not.toContain("管理中心");
  });

  it("adds one quiet management entry for owners without replacing use navigation", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("deployment unavailable"));
    await act(async () => root.render(<AppShell
      route={{ name: "admin-overview" }} account={{ ...member, role: "platform_owner" }}
    ><p>内容</p></AppShell>));
    const navigation = container.querySelector(".product-nav")?.textContent || "";
    expect(navigation).toContain("Agent 大脑");
    expect(navigation).toContain("专业 Agent");
    expect(navigation).toContain("历史任务");
    expect(navigation).toContain("企业账号");
    expect(navigation).toContain("管理中心");
    expect(container.querySelector<HTMLAnchorElement>('a[href="/admin"]')).not.toBeNull();
  });
});
