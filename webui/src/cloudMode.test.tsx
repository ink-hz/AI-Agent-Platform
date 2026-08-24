/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";
import App from "./App";
import type { Account } from "./auth";
import { navigate } from "./router";


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
  document.querySelector('meta[name="platform-identity-mode"]')?.remove();
  window.history.replaceState({}, "", "/");
});


describe("cloud replica mode", () => {
  it("opens the authenticated cloud root as the continuous Agent Brain composer", async () => {
    const meta = document.createElement("meta");
    meta.name = "platform-identity-mode";
    meta.content = "enabled";
    document.head.append(meta);
    window.history.replaceState({}, "", "/");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/v1/account")) return new Response(JSON.stringify({
        internal_user_id: "member", display_name: "成员", role: "member",
        departments: [], gender: null,
        observation_agent_ids: [], directory_freshness: "fresh",
        hard_stale_read_only: false, csrf_token: "csrf",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.includes("/api/v1/conversations")) return new Response(JSON.stringify({
        items: [], next_cursor: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify({
        mode: "local", read_only: false, auth: "dingtalk",
        freshness: "current", last_success_at: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.querySelector("#brain-heading")?.textContent).toBe("Agent 大脑");
    expect(container.querySelector<HTMLTextAreaElement>("#brain-request")?.disabled).toBe(false);
    expect(container.textContent).toContain("开始对话");
    expect(container.textContent).not.toContain("Agent 集群总览");
  });

  it("returns an expired usage route to login with its safe Mission path", async () => {
    const meta = document.createElement("meta");
    meta.name = "platform-identity-mode";
    meta.content = "enabled";
    document.head.append(meta);
    window.history.replaceState({}, "", "/missions/8c13c965-1b60-472e-b275-199987d1d109");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(`${window.location.pathname}${window.location.search}`).toBe(
      "/login?return_path=%2Fmissions%2F8c13c965-1b60-472e-b275-199987d1d109",
    );
  });

  it("keeps the loaded account across authenticated route transitions", async () => {
    const meta = document.createElement("meta");
    meta.name = "platform-identity-mode";
    meta.content = "enabled";
    document.head.append(meta);
    window.history.replaceState({}, "", "/account");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/v1/account")) return new Response(JSON.stringify({
        internal_user_id: "owner", display_name: "苍渊", role: "platform_owner",
        departments: [], gender: null,
        observation_agent_ids: [], directory_freshness: "fresh",
        hard_stale_read_only: false, csrf_token: "csrf",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.endsWith("/api/v1/manage/users")) return new Response(JSON.stringify({ users: [] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
      return new Response(JSON.stringify({
        mode: "local", read_only: false, auth: "dingtalk",
        freshness: "current", last_success_at: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(container.textContent).toContain("苍渊");

    await act(async () => {
      navigate("/admin/identity");
      await Promise.resolve();
      await Promise.resolve();
    });

    const accountCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/api/v1/account"));
    expect(accountCalls).toHaveLength(1);
    expect(container.textContent).not.toContain("正在验证企业身份");
  });

  it("offers recovery actions and retries account bootstrap in place", async () => {
    const meta = document.createElement("meta");
    meta.name = "platform-identity-mode";
    meta.content = "enabled";
    document.head.append(meta);
    window.history.replaceState({}, "", "/account");
    let accountReads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/v1/account")) {
        accountReads += 1;
        if (accountReads === 1) return new Response("{}", { status: 500 });
        return new Response(JSON.stringify({
          internal_user_id: "owner", display_name: "苍渊", role: "platform_owner",
          departments: [], gender: null,
          observation_agent_ids: [], directory_freshness: "fresh",
          hard_stale_read_only: false, csrf_token: "csrf",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        mode: "local", read_only: false, auth: "dingtalk",
        freshness: "current", last_success_at: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.textContent).toContain("暂时无法进入平台");
    expect(container.textContent).toContain("重新尝试");
    expect(container.querySelector<HTMLAnchorElement>('a[href="/login"]')?.textContent).toBe("重新登录");

    const retry = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "重新尝试");
    expect(retry).toBeDefined();
    await act(async () => {
      retry?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(accountReads).toBe(2);
    expect(container.textContent).toContain("苍渊");
    expect(container.textContent).not.toContain("暂时无法进入平台");
  });

  it("shows a compact read-only banner and hides Review navigation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "cloud-replica", read_only: true, auth: "ssh-tunnel",
      freshness: "current", last_success_at: "2026-08-11T08:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "admin-overview" }}><p>内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("云端脱敏只读副本");
    expect(container.textContent).toContain("数据已同步");
    expect(container.textContent).not.toContain("复审闭环");
    expect(container.querySelector(".cloud-replica-banner")?.className).toContain("is-current");
  });

  it("keeps cloud replica status out of the Agent Brain workspace", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "cloud-replica", read_only: true, auth: "ssh-tunnel",
      freshness: "current", last_success_at: "2026-08-24T06:43:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "brain" }}><p>Agent 大脑内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("Agent 大脑内容");
    expect(container.textContent).not.toContain("云端脱敏只读副本");
    expect(container.textContent).not.toContain("数据已同步");
    expect(container.textContent).not.toContain("最近同步");
    expect(container.querySelector(".cloud-replica-banner")).toBeNull();
  });

  it("visibly distinguishes stale replica data", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "cloud-replica", read_only: true, auth: "ssh-tunnel",
      freshness: "stale", last_success_at: "2026-08-11T07:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "admin-sessions" }}><p>内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("数据已过期");
    expect(container.querySelector(".cloud-replica-banner")?.className).toContain("is-stale");
  });

  it("derives member and viewer navigation from the server account", async () => {
    const member: Account = {
      internal_user_id: "member", display_name: "成员", role: "member",
      departments: [], gender: null,
      observation_agent_ids: [], directory_freshness: "fresh",
      hard_stale_read_only: false, csrf_token: "csrf",
    };
    await act(async () => root.render(
      <AppShell route={{ name: "account" }} account={member}><p>内容</p></AppShell>,
    ));
    expect(container.querySelector(".product-nav")?.textContent).toBe("Agent 大脑专业 Agent企业账号");

    const viewer: Account = {
      ...member, display_name: "观察者", role: "management_viewer",
      observation_agent_ids: ["ai-fae-agent", "hr-bot"],
    };
    await act(async () => root.render(
      <AppShell route={{ name: "admin-governance" }} account={viewer}><p>内容</p></AppShell>,
    ));
    const navigation = container.querySelector(".product-nav")?.textContent || "";
    expect(navigation).toBe("Agent 大脑专业 Agent企业账号");
    expect(navigation).not.toContain("管理中心");
  });

  it("shows the server-enforced hard-stale read-only state", async () => {
    const owner: Account = {
      internal_user_id: "owner", display_name: "苍渊", role: "platform_owner",
      departments: [], gender: null,
      observation_agent_ids: [], directory_freshness: "hard_stale",
      hard_stale_read_only: true, csrf_token: "csrf",
    };
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("deployment unavailable"));
    await act(async () => root.render(
      <AppShell route={{ name: "brain" }} account={owner}><p>内容</p></AppShell>,
    ));
    expect(container.querySelector(".hard-stale-banner")?.textContent).toContain("只读访问");
  });

  it("gives a platform administrator full manager navigation and deployment status", async () => {
    const administrator: Account = {
      internal_user_id: "admin", display_name: "管理员", role: "platform_admin",
      departments: [], gender: null,
      observation_agent_ids: [], directory_freshness: "fresh",
      hard_stale_read_only: false, csrf_token: "csrf",
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "local", read_only: false, auth: "dingtalk",
      freshness: "current", last_success_at: null,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "admin-identity" }} account={administrator}><p>内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    const navigation = container.querySelector(".product-nav")?.textContent || "";
    expect(navigation).toContain("Agent 大脑");
    expect(navigation).toContain("管理中心");
    expect(container.querySelector(".admin-nav")?.textContent).toContain("身份管理");
    expect(fetchMock).toHaveBeenCalled();
  });

  it("admits a platform administrator to the identity route", async () => {
    const meta = document.createElement("meta");
    meta.name = "platform-identity-mode";
    meta.content = "enabled";
    document.head.append(meta);
    window.history.replaceState({}, "", "/admin/identity");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/v1/account")) return new Response(JSON.stringify({
        internal_user_id: "admin", display_name: "管理员", role: "platform_admin",
        departments: [], gender: null,
        observation_agent_ids: [], directory_freshness: "fresh",
        hard_stale_read_only: false, csrf_token: "csrf",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.endsWith("/api/v1/manage/users")) return new Response(JSON.stringify({ users: [] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
      return new Response(JSON.stringify({
        mode: "local", read_only: false, auth: "dingtalk",
        freshness: "current", last_success_at: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });

    await act(async () => root.render(<App />));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("身份与观察范围");
    expect(container.textContent).not.toContain("无权访问");
  });
});
