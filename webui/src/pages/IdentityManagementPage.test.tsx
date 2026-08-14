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

const administrator: Account = {
  ...owner, internal_user_id: "admin-actor", display_name: "管理员",
  role: "platform_admin",
};

const managedUsers = [{
  internal_user_id: "member", display_name: "测试成员",
  role: "member", status: "active", scopes: [],
}, {
  internal_user_id: "viewer", display_name: "观察者",
  role: "management_viewer", status: "active", scopes: ["ai-fae-agent"],
}, {
  internal_user_id: "admin-target", display_name: "目标管理员",
  role: "platform_admin", status: "active", scopes: [],
}, {
  internal_user_id: "owner", display_name: "苍渊",
  role: "platform_owner", status: "active", scopes: [],
}];

function articleFor(container: HTMLDivElement, name: string): HTMLElement {
  const article = [...container.querySelectorAll("article")]
    .find((item) => item.querySelector("strong")?.textContent === name);
  if (!article) throw new Error(`missing article for ${name}`);
  return article;
}

function usersResponse(users = managedUsers): Response {
  return new Response(JSON.stringify({ users }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

function indeterminateResponse(requestId: string): Response {
  return new Response(JSON.stringify({
    detail: { code: "management_mutation_indeterminate", request_id: requestId },
  }), { status: 503, headers: { "Content-Type": "application/json" } });
}


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

  it("gives the owner exact administrator controls only for member and administrator targets", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: managedUsers }), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect([...articleFor(container, "测试成员").querySelectorAll("button")]
      .map((button) => button.textContent)).toContain("设为平台管理员");
    expect([...articleFor(container, "目标管理员").querySelectorAll("button")]
      .map((button) => button.textContent)).toEqual(["撤销平台管理员"]);
    expect(articleFor(container, "苍渊").querySelector("button")).toBeNull();
    expect([...articleFor(container, "观察者").querySelectorAll("button")]
      .some((button) => button.textContent?.includes("平台管理员"))).toBe(false);
  });

  it("lets an administrator manage viewers and scopes but never administrator roles", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: managedUsers }), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));

    await act(async () => root.render(<IdentityManagementPage account={administrator} />));

    expect(container.textContent).toContain("身份与观察范围");
    expect([...container.querySelectorAll("button")]
      .some((button) => button.textContent?.includes("平台管理员"))).toBe(false);
    const memberViewerButton = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为只读观察者");
    expect(memberViewerButton).toBeDefined();
    expect(articleFor(container, "观察者").querySelector("input[aria-label='观察者的新 Agent 范围']")).not.toBeNull();
    expect([...articleFor(container, "观察者").querySelectorAll("button")]
      .some((button) => button.textContent === "撤销 ai-fae-agent")).toBe(true);
    expect(articleFor(container, "目标管理员").querySelector("button")).toBeNull();
    expect(articleFor(container, "苍渊").querySelector("button")).toBeNull();

    const reason = container.querySelector("input[aria-label='变更原因']") as HTMLInputElement;
    await act(async () => {
      reason.value = "审批通过";
      reason.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(memberViewerButton?.hasAttribute("disabled")).toBe(false);
  });

  it("shows click-level administrator success only after the server confirms and refreshes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(usersResponse(managedUsers.map((user) => user.internal_user_id === "member"
        ? { ...user, role: "platform_admin" }
        : user)));
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("变更成功，服务端已记录审计事件。");
    expect(container.querySelector("[role='status']")?.classList).toContain("is-success");
    expect(articleFor(container, "测试成员").textContent).toContain("平台管理员");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({
      reason: "admin_access_approved",
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
  });

  it.each([
    ["assignment", "测试成员", "设为平台管理员", "POST", "admin_access_approved", "platform_admin", "撤销平台管理员"],
    ["revocation", "目标管理员", "撤销平台管理员", "DELETE", "admin_access_revoked", "member", "设为平台管理员"],
  ] as const)("reconciles indeterminate administrator %s with the identical operation ID and refreshed role", async (
    _scenario, targetName, actionLabel, method, reason, refreshedRole, refreshedAction,
  ) => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const refreshedUsers = managedUsers.map((user) => user.display_name === targetName
      ? { ...user, role: refreshedRole }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(indeterminateResponse(requestId))
      .mockResolvedValueOnce(usersResponse(refreshedUsers))
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(usersResponse(refreshedUsers));
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, targetName).querySelectorAll("button")]
      .find((button) => button.textContent === actionLabel);
    await act(async () => action?.click());

    const mutationCalls = fetchMock.mock.calls.filter((call) => call[1]?.method === method);
    expect(mutationCalls).toHaveLength(2);
    expect(mutationCalls.map((call) => JSON.parse(String(call[1]?.body)))).toEqual([
      { reason, request_id: requestId },
      { reason, request_id: requestId },
    ]);
    expect(container.textContent).toContain("变更结果曾无法确认；已使用同一请求重试并刷新确认生效。");
    expect(container.querySelector("[role='status']")?.classList).toContain("is-success");
    expect(container.textContent).not.toContain("未执行任何变更");
    expect([...articleFor(container, targetName).querySelectorAll("button")]
      .some((button) => button.textContent === refreshedAction)).toBe(true);
  });

  it("retains an unknown administrator operation for an identical manual replay", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === "member"
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(indeterminateResponse(requestId))
      .mockResolvedValueOnce(usersResponse(assignedUsers))
      .mockResolvedValueOnce(indeterminateResponse(requestId))
      .mockResolvedValueOnce(usersResponse(assignedUsers))
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("管理员变更结果仍未知；已刷新当前角色，请使用同一请求重试确认。");
    expect(container.querySelector("[role='status']")?.classList).toContain("is-error");
    const retry = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "使用同一请求重试确认");
    await act(async () => retry?.click());

    const mutationBodies = fetchMock.mock.calls
      .filter((call) => call[1]?.method === "POST")
      .map((call) => JSON.parse(String(call[1]?.body)));
    expect(mutationBodies).toHaveLength(3);
    expect(mutationBodies.every((body) => body.request_id === requestId)).toBe(true);
    expect(container.textContent).toContain("变更结果曾无法确认；已使用同一请求重试并刷新确认生效。");
  });

  it("keeps the outcome unknown without claiming a refresh when reconciliation reads fail", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const unavailable = () => new Response(JSON.stringify({
      detail: "identity management unavailable",
    }), { status: 503, headers: { "Content-Type": "application/json" } });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(indeterminateResponse(requestId))
      .mockResolvedValueOnce(unavailable())
      .mockResolvedValueOnce(indeterminateResponse(requestId))
      .mockResolvedValueOnce(unavailable());
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("管理员变更结果仍未知；当前角色刷新失败，请使用同一请求重试确认。");
    expect(container.textContent).not.toContain("未执行任何变更");
  });

  it("shows click-level administrator errors without optimistic success", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: "identity management unavailable",
      }), { status: 503, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("审计或目录服务暂不可用，未执行任何变更。");
    expect(container.querySelector("[role='status']")?.classList).toContain("is-error");
    expect(container.textContent).not.toContain("变更成功");
    expect(container.textContent).not.toContain("结果仍未知");
  });
});
