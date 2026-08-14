/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";
import type { Account } from "./auth";


let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});


describe("cloud replica mode", () => {
  it("shows a compact read-only banner and hides Review navigation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "cloud-replica", read_only: true, auth: "ssh-tunnel",
      freshness: "current", last_success_at: "2026-08-11T08:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "overview" }}><p>内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("云端脱敏只读副本");
    expect(container.textContent).toContain("数据已同步");
    expect(container.textContent).not.toContain("复审闭环");
    expect(container.querySelector(".cloud-replica-banner")?.className).toContain("is-current");
  });

  it("visibly distinguishes stale replica data", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "cloud-replica", read_only: true, auth: "ssh-tunnel",
      freshness: "stale", last_success_at: "2026-08-11T07:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "sessions" }}><p>内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("数据已过期");
    expect(container.querySelector(".cloud-replica-banner")?.className).toContain("is-stale");
  });

  it("derives member and viewer navigation from the server account", async () => {
    const member: Account = {
      internal_user_id: "member", display_name: "成员", role: "member",
      observation_agent_ids: [], directory_freshness: "fresh",
      hard_stale_read_only: false, csrf_token: "csrf",
    };
    await act(async () => root.render(
      <AppShell route={{ name: "account" }} account={member}><p>内容</p></AppShell>,
    ));
    expect(container.querySelector(".product-nav")?.textContent).toBe("企业账号");

    const viewer: Account = {
      ...member, display_name: "观察者", role: "management_viewer",
      observation_agent_ids: ["ai-fae-agent", "hr-bot"],
    };
    await act(async () => root.render(
      <AppShell route={{ name: "governance" }} account={viewer}><p>内容</p></AppShell>,
    ));
    const navigation = container.querySelector(".product-nav")?.textContent || "";
    expect(navigation).toContain("ai-fae-agent 运行");
    expect(navigation).toContain("hr-bot 复审");
    expect(navigation).toContain("治理审计");
    expect(navigation).not.toContain("总览");
    expect(navigation).not.toContain("Session");
  });

  it("shows the server-enforced hard-stale read-only state", async () => {
    const owner: Account = {
      internal_user_id: "owner", display_name: "苍渊", role: "platform_owner",
      observation_agent_ids: [], directory_freshness: "hard_stale",
      hard_stale_read_only: true, csrf_token: "csrf",
    };
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("deployment unavailable"));
    await act(async () => root.render(
      <AppShell route={{ name: "overview" }} account={owner}><p>内容</p></AppShell>,
    ));
    expect(container.querySelector(".hard-stale-banner")?.textContent).toContain("只读访问");
  });
});
