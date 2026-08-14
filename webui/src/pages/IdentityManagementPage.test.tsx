/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { IdentityManagementPage } from "./IdentityManagementPage";


const owner: Account = {
  internal_user_id: "62a31b32-2a92-47d4-9f79-f0c61bca12aa", display_name: "苍渊",
  role: "platform_owner", observation_agent_ids: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};


describe("IdentityManagementPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it("is owner-only and requires a reason before changing viewer access", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [{
      internal_user_id: "9e378763-287e-4dda-88a8-0b338f629af3", display_name: "测试成员",
      role: "member", status: "active", scopes: [],
    }, {
      internal_user_id: "7319a8c6-ee88-447e-bdce-dc9ee9e0a561", display_name: "观察者",
      role: "management_viewer", status: "active", scopes: ["ai-fae-agent"],
    }] }), { status: 200, headers: { "Content-Type": "application/json" } })));
    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    expect(container.textContent).toContain("身份与观察范围");
    const action = [...container.querySelectorAll("button")].find((item) => item.textContent === "设为只读观察者");
    expect(action?.hasAttribute("disabled")).toBe(true);
    const reason = container.querySelector("input[aria-label='变更原因']") as HTMLInputElement;
    await act(async () => {
      reason.value = "管理层批准演示访问";
      reason.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(action?.hasAttribute("disabled")).toBe(false);
    expect(container.textContent).toContain("ai-fae-agent");
    expect(container.querySelector("input[aria-label='观察者的新 Agent 范围']")).not.toBeNull();
    expect([...container.querySelectorAll("button")].some((item) => item.textContent === "撤销 ai-fae-agent")).toBe(true);
  });

  it("renders backend permission and audit failures without optimistic success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "required audit unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } })));
    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    expect(container.textContent).toContain("审计或目录服务暂不可用");
    expect(container.textContent).not.toContain("变更成功");
  });
});
