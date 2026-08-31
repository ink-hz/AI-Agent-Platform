/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { IdentityManagementPage } from "./IdentityManagementPage";


const owner: Account = {
  internal_user_id: "62a31b32-2a92-47d4-9f79-f0c61bca12aa", display_name: "苍渊",
  departments: [], gender: null,
  role: "platform_owner", observation_agent_ids: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};
const pendingAdministratorStorageKey = `platform.identity.pending-administrator.v1:${owner.internal_user_id}`;
const memberId = "1bf6c7ef-125d-418f-9835-e3bd70a90778";
const viewerId = "c1fc8f7b-fc86-407a-8c42-e851e9d776a5";
const administratorId = "1b129d2e-e701-481a-8e36-d78154b1ebbc";

const administrator: Account = {
  ...owner, internal_user_id: "admin-actor", display_name: "管理员",
  role: "platform_admin",
};

const managedUsers = [{
  internal_user_id: memberId, display_name: "测试成员",
  role: "member", status: "active", scopes: [],
}, {
  internal_user_id: viewerId, display_name: "观察者",
  role: "management_viewer", status: "active", scopes: ["ai-fae-agent"],
}, {
  internal_user_id: administratorId, display_name: "目标管理员",
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


function withPartnerReads(fetchMock: ReturnType<typeof vi.fn>): ReturnType<typeof vi.fn> {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (!init?.method || init.method === "GET") {
      if (path.endsWith("/api/v1/manage/partners/organizations")) {
        return Promise.resolve(new Response(JSON.stringify({ organizations: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        }));
      }
      if (path.endsWith("/api/v1/manage/partners/operators")) {
        return Promise.resolve(new Response(JSON.stringify({ operators: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        }));
      }
      if (path.endsWith("/api/v1/manage/partners/binding-requests")) {
        return Promise.resolve(new Response(JSON.stringify({ binding_requests: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        }));
      }
    }
    return fetchMock(input, init);
  });
}


function isolatedAdministratorStorage(
  readAdministrator: () => string | null,
  writeAdministrator: (value: string) => void,
  removeAdministrator: () => void,
) {
  const other = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => key === pendingAdministratorStorageKey
      ? readAdministrator()
      : other.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      if (key === pendingAdministratorStorageKey) writeAdministrator(value);
      else other.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      if (key === pendingAdministratorStorageKey) removeAdministrator();
      else other.delete(key);
    }),
    clear: vi.fn(() => other.clear()),
  };
}


describe("IdentityManagementPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("is owner-only and requires a reason before changing viewer access", async () => {
    vi.stubGlobal("fetch", withPartnerReads(vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [{
      internal_user_id: "9e378763-287e-4dda-88a8-0b338f629af3", display_name: "测试成员",
      role: "member", status: "active", scopes: [],
    }, {
      internal_user_id: "7319a8c6-ee88-447e-bdce-dc9ee9e0a561", display_name: "观察者",
      role: "management_viewer", status: "active", scopes: ["ai-fae-agent"],
    }] }), { status: 200, headers: { "Content-Type": "application/json" } }))));
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
    vi.stubGlobal("fetch", withPartnerReads(vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "required audit unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } }))));
    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    expect(container.textContent).toContain("审计或目录服务暂不可用");
    expect(container.textContent).not.toContain("变更成功");
  });

  it("gives the owner exact administrator controls only for member and administrator targets", async () => {
    vi.stubGlobal("fetch", withPartnerReads(vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: managedUsers }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect([...articleFor(container, "测试成员").querySelectorAll("button")]
      .map((button) => button.textContent)).toContain("设为平台管理员");
    expect([...articleFor(container, "目标管理员").querySelectorAll("button")]
      .map((button) => button.textContent)).toEqual(["撤销平台管理员"]);
    expect(articleFor(container, "苍渊").querySelector("button")).toBeNull();
    expect([...articleFor(container, "观察者").querySelectorAll("button")]
      .some((button) => button.textContent?.includes("平台管理员"))).toBe(false);
  });

  it("lets the owner revoke an inactive administrator but never assign an inactive member", async () => {
    const inactiveUsers = [
      ...managedUsers,
      {
        internal_user_id: "f9ed17df-9496-4923-979a-aec1e465dc58",
        display_name: "离职管理员",
        role: "platform_admin",
        status: "inactive",
        scopes: [],
      },
      {
        internal_user_id: "61acb7d7-fd4f-4cb1-b9c8-4bc7e7b36faf",
        display_name: "离职成员",
        role: "member",
        status: "inactive",
        scopes: [],
      },
    ];
    vi.stubGlobal("fetch", withPartnerReads(vi.fn().mockResolvedValue(usersResponse(inactiveUsers))));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect([...articleFor(container, "离职管理员").querySelectorAll("button")]
      .map((button) => button.textContent)).toEqual(["撤销平台管理员"]);
    expect([...articleFor(container, "离职成员").querySelectorAll("button")]
      .some((button) => button.textContent?.includes("平台管理员"))).toBe(false);
  });

  it("lets an administrator manage viewers and scopes but never administrator roles", async () => {
    vi.stubGlobal("fetch", withPartnerReads(vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: managedUsers }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))));

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
      .mockResolvedValueOnce(usersResponse(managedUsers.map((user) => user.internal_user_id === memberId
        ? { ...user, role: "platform_admin" }
        : user)));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

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
    expect(sessionStorage.getItem(pendingAdministratorStorageKey)).toBeNull();
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
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

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
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
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
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("管理员变更结果仍未知；已刷新当前角色，请使用同一请求重试确认。");
    expect(container.querySelector("[role='status']")?.classList).toContain("is-error");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toEqual({
      version: 1,
      kind: "pending_replay",
      target_internal_user_id: memberId,
      action: "assign",
      request_id: requestId,
    });
    const retry = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "使用同一请求重试确认");
    await act(async () => retry?.click());

    const mutationBodies = fetchMock.mock.calls
      .filter((call) => call[1]?.method === "POST")
      .map((call) => JSON.parse(String(call[1]?.body)));
    expect(mutationBodies).toHaveLength(3);
    expect(mutationBodies.every((body) => body.request_id === requestId)).toBe(true);
    expect(container.textContent).toContain("变更结果曾无法确认；已使用同一请求重试并刷新确认生效。");
    expect(sessionStorage.getItem(pendingAdministratorStorageKey)).toBeNull();
  });

  it("restores a persisted immutable operation after reload without creating a new ID", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    sessionStorage.setItem(pendingAdministratorStorageKey, JSON.stringify({
      version: 1,
      kind: "pending_replay",
      target_internal_user_id: memberId,
      action: "assign",
      request_id: requestId,
    }));
    const randomUUID = vi.spyOn(globalThis.crypto, "randomUUID");
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    const freshAction = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    expect(freshAction?.hasAttribute("disabled")).toBe(true);
    const retry = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "使用同一请求重试确认");
    await act(async () => retry?.click());

    expect(randomUUID).not.toHaveBeenCalled();
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      reason: "admin_access_approved",
      request_id: requestId,
    });
    expect(sessionStorage.getItem(pendingAdministratorStorageKey)).toBeNull();
  });

  it.each([
    ["malformed JSON", "{"],
    ["unknown shape", JSON.stringify({ version: 1, kind: "unexpected" })],
    ["legacy replayable kind", JSON.stringify({
      version: 1, kind: "pending", target_internal_user_id: memberId,
      action: "assign", request_id: "47f493ac-e830-4fe7-9e7d-58b1dfcebd56",
    })],
    ["extra field", JSON.stringify({
      version: 1, kind: "pending_replay", target_internal_user_id: memberId,
      action: "assign", request_id: "47f493ac-e830-4fe7-9e7d-58b1dfcebd56",
      csrf_token: "must-not-be-accepted",
    })],
    ["invalid request ID", JSON.stringify({
      version: 1, kind: "pending_replay", target_internal_user_id: memberId,
      action: "assign", request_id: "not-a-uuid",
    })],
    ["invalid target ID", JSON.stringify({
      version: 1, kind: "pending_replay", target_internal_user_id: "not-a-uuid",
      action: "assign", request_id: "47f493ac-e830-4fe7-9e7d-58b1dfcebd56",
    })],
  ])("fails closed when persisted administrator state has %s", async (_scenario, value) => {
    sessionStorage.setItem(pendingAdministratorStorageKey, value);
    vi.stubGlobal("fetch", withPartnerReads(vi.fn().mockResolvedValue(usersResponse())));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(container.textContent).toContain("无法验证待处理的管理员操作；已停止新的管理员变更，请手动核查。");
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toEqual({
      version: 1, kind: "integrity_failure",
    });
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
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("管理员变更结果仍未知；当前角色刷新失败，请使用同一请求重试确认。");
    expect(container.textContent).not.toContain("未执行任何变更");
  });

  it("fails closed without storing or replaying either request ID when the server echo mismatches", async () => {
    const clientRequestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    const echoedRequestId = "cbabef76-43c2-49a5-ae88-ed225caca69c";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(clientRequestId);
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(indeterminateResponse(echoedRequestId))
      .mockResolvedValueOnce(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(container.textContent).toContain("管理员变更响应校验失败；已停止新的管理员变更，请手动核查。");
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);
    const stored = String(sessionStorage.getItem(pendingAdministratorStorageKey));
    expect(JSON.parse(stored)).toEqual({ version: 1, kind: "integrity_failure" });
    expect(stored).not.toContain(clientRequestId);
    expect(stored).not.toContain(echoedRequestId);
  });

  it("keeps a confirmed mutation non-replayable when durable replacement and cleanup fail", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    let persisted: string | null = null;
    vi.stubGlobal("sessionStorage", isolatedAdministratorStorage(
      () => persisted,
      (value: string) => {
        if (persisted === null) {
          persisted = value;
          return;
        }
        throw new DOMException("storage unavailable");
      },
      () => { throw new DOMException("storage unavailable"); },
    ));
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValue(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
    expect(container.textContent).not.toContain("使用同一请求重试确认");

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
  });

  it("keeps a durable confirmed state non-replayable when cleanup fails", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    let persisted: string | null = null;
    vi.stubGlobal("sessionStorage", isolatedAdministratorStorage(
      () => persisted,
      (value: string) => { persisted = value; },
      () => { throw new DOMException("storage unavailable"); },
    ));
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValue(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "confirmed_needs_refresh", request_id: requestId,
    });

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "confirmed_needs_refresh", request_id: requestId,
    });
  });

  it("keeps an ID-mismatched mutation non-replayable when the integrity write fails", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    const echoedRequestId = "cbabef76-43c2-49a5-ae88-ed225caca69c";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    let persisted: string | null = null;
    vi.stubGlobal("sessionStorage", isolatedAdministratorStorage(
      () => persisted,
      (value: string) => {
        if (persisted === null) {
          persisted = value;
          return;
        }
        throw new DOMException("storage unavailable");
      },
      () => { throw new DOMException("storage unavailable"); },
    ));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(indeterminateResponse(echoedRequestId))
      .mockResolvedValue(usersResponse());
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
    expect(container.textContent).not.toContain("使用同一请求重试确认");

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
  });

  it("does not expose replay when the uncertainty transition cannot be persisted", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    let persisted: string | null = null;
    vi.stubGlobal("sessionStorage", isolatedAdministratorStorage(
      () => persisted,
      (value: string) => {
        if (persisted === null) {
          persisted = value;
          return;
        }
        throw new DOMException("storage unavailable");
      },
      () => { persisted = null; },
    ));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockRejectedValueOnce(new TypeError("network connection lost"))
      .mockResolvedValueOnce(usersResponse());
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("操作保持不可重放");
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("keeps an applied-role in-flight mutation blocked without terminal audit evidence", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    sessionStorage.setItem(pendingAdministratorStorageKey, JSON.stringify({
      version: 1,
      kind: "inflight_no_replay",
      target_internal_user_id: memberId,
      action: "assign",
      request_id: requestId,
    }));
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse(assignedUsers))
      .mockResolvedValueOnce(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(container.textContent).not.toContain("变更已确认");
    expect(container.textContent).toContain("人工核查治理审计");
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(0);
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);
    const refresh = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "刷新当前角色");
    await act(async () => refresh?.click());

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(0);
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
    expect(container.textContent).not.toContain("变更已确认");
    expect(container.textContent).toContain("人工核查治理审计");
  });

  it("keeps inflight blocked when a different operation produced the expected role", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    sessionStorage.setItem(pendingAdministratorStorageKey, JSON.stringify({
      version: 1,
      kind: "inflight_no_replay",
      target_internal_user_id: administratorId,
      action: "revoke",
      request_id: requestId,
    }));
    const memberUsers = managedUsers.map((user) => user.internal_user_id === administratorId
      ? { ...user, role: "member" }
      : user);
    const fetchMock = vi.fn().mockResolvedValue(usersResponse(memberUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(container.textContent).not.toContain("变更已确认");
    expect(container.textContent).toContain("人工核查治理审计");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "DELETE")).toHaveLength(0);
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("does not make an unclassified client failure replayable", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockRejectedValueOnce(new Error("unexpected client failure"));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });
  });

  it("keeps terminal cleanup failure blocked in the non-replayable state", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    let persisted: string | null = null;
    vi.stubGlobal("sessionStorage", isolatedAdministratorStorage(
      () => persisted,
      (value: string) => { persisted = value; },
      () => { throw new DOMException("storage unavailable"); },
    ));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "forbidden" }), {
        status: 403, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValue(usersResponse());
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(persisted))).toMatchObject({
      version: 1, kind: "inflight_no_replay", request_id: requestId,
    });

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(container.textContent).not.toContain("使用同一请求重试确认");
  });

  it("fails closed on a mismatched request ID echoed during restored replay", async () => {
    const clientRequestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    const echoedRequestId = "cbabef76-43c2-49a5-ae88-ed225caca69c";
    sessionStorage.setItem(pendingAdministratorStorageKey, JSON.stringify({
      version: 1,
      kind: "pending_replay",
      target_internal_user_id: memberId,
      action: "assign",
      request_id: clientRequestId,
    }));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(indeterminateResponse(echoedRequestId))
      .mockResolvedValueOnce(usersResponse());
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const retry = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "使用同一请求重试确认");
    await act(async () => retry?.click());

    expect(container.textContent).toContain("管理员变更响应校验失败；已停止新的管理员变更，请手动核查。");
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    const stored = String(sessionStorage.getItem(pendingAdministratorStorageKey));
    expect(JSON.parse(stored)).toEqual({ version: 1, kind: "integrity_failure" });
    expect(stored).not.toContain(clientRequestId);
    expect(stored).not.toContain(echoedRequestId);
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
  });

  it("keeps the storage-integrity warning when an uncertain replay cannot be retained", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    const persisted = JSON.stringify({
      version: 1,
      kind: "pending_replay",
      target_internal_user_id: memberId,
      action: "assign",
      request_id: requestId,
    });
    vi.stubGlobal("sessionStorage", isolatedAdministratorStorage(
      () => persisted,
      () => { throw new DOMException("storage unavailable"); },
      () => {},
    ));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: "identity management unavailable",
      }), { status: 503, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(usersResponse());
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const retry = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "使用同一请求重试确认");
    await act(async () => retry?.click());

    expect(container.textContent).toContain("无法保存待处理的管理员操作；已停止新的管理员变更，请手动核查。");
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it.each([
    ["transport rejection", "reject"],
    ["unclassified proxy 502", "502"],
  ] as const)("retains and reload-replays the identical operation after %s", async (
    _scenario, failure,
  ) => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    const randomUUID = vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn().mockResolvedValueOnce(usersResponse());
    if (failure === "reject") {
      fetchMock.mockRejectedValueOnce(new TypeError("network connection lost"));
    } else {
      fetchMock.mockResolvedValueOnce(new Response("proxy failure", { status: 502 }));
    }
    fetchMock
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("管理员变更结果仍未知；已刷新当前角色，请使用同一请求重试确认。");
    expect(container.textContent).not.toContain("未执行任何变更");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toEqual({
      version: 1,
      kind: "pending_replay",
      target_internal_user_id: memberId,
      action: "assign",
      request_id: requestId,
    });

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const retry = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "使用同一请求重试确认");
    await act(async () => retry?.click());

    const mutationBodies = fetchMock.mock.calls
      .filter((call) => call[1]?.method === "POST")
      .map((call) => JSON.parse(String(call[1]?.body)));
    expect(mutationBodies).toEqual([
      { reason: "admin_access_approved", request_id: requestId },
      { reason: "admin_access_approved", request_id: requestId },
    ]);
    expect(randomUUID).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(pendingAdministratorStorageKey)).toBeNull();
    expect(container.textContent).toContain("变更结果曾无法确认；已使用同一请求重试并刷新确认生效。");
  });

  it("shows click-level administrator errors without optimistic success", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: "required audit unavailable",
      }), { status: 503, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    expect(container.textContent).toContain("审计或目录服务暂不可用，未执行任何变更。");
    expect(container.querySelector("[role='status']")?.classList).toContain("is-error");
    expect(container.textContent).not.toContain("变更成功");
    expect(container.textContent).not.toContain("结果仍未知");
    expect(sessionStorage.getItem(pendingAdministratorStorageKey)).toBeNull();
  });

  it.each([
    ["assignment", "测试成员", "设为平台管理员", "POST", "platform_admin", "撤销平台管理员"],
    ["revocation", "目标管理员", "撤销平台管理员", "DELETE", "member", "设为平台管理员"],
  ] as const)("reports confirmed administrator %s separately when role refresh fails", async (
    _scenario, targetName, actionLabel, method, refreshedRole, refreshedAction,
  ) => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const unavailable = () => new Response(JSON.stringify({
      detail: "identity management unavailable",
    }), { status: 503, headers: { "Content-Type": "application/json" } });
    const refreshedUsers = managedUsers.map((user) => user.display_name === targetName
      ? { ...user, role: refreshedRole }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(unavailable())
      .mockResolvedValueOnce(usersResponse(refreshedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, targetName).querySelectorAll("button")]
      .find((button) => button.textContent === actionLabel);
    await act(async () => action?.click());

    expect(container.textContent).toContain("管理员变更已由服务端确认，但当前角色刷新失败。");
    expect(container.textContent).not.toContain("未执行任何变更");
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toEqual({
      version: 1,
      kind: "confirmed_needs_refresh",
      target_internal_user_id: targetName === "测试成员" ? memberId : administratorId,
      action: targetName === "测试成员" ? "assign" : "revoke",
      request_id: requestId,
    });
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === method)).toHaveLength(1);
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === method)).toHaveLength(1);
    expect(container.textContent).toContain("变更已确认，当前角色已刷新。");
    expect([...articleFor(container, targetName).querySelectorAll("button")]
      .some((button) => button.textContent === refreshedAction)).toBe(true);
    expect(sessionStorage.getItem(pendingAdministratorStorageKey)).toBeNull();
  });

  it("keeps confirmed role mismatch blocked across consecutive refreshes and reload", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const assignedUsers = managedUsers.map((user) => user.internal_user_id === memberId
      ? { ...user, role: "platform_admin" }
      : user);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(new Response("{}", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(usersResponse())
      .mockResolvedValueOnce(usersResponse(assignedUsers));
    vi.stubGlobal("fetch", withPartnerReads(fetchMock));

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    const action = [...articleFor(container, "测试成员").querySelectorAll("button")]
      .find((button) => button.textContent === "设为平台管理员");
    await act(async () => action?.click());

    const expectedState = {
      version: 1,
      kind: "confirmed_needs_refresh",
      target_internal_user_id: memberId,
      action: "assign",
      request_id: requestId,
    };
    expect(container.textContent).toContain("管理员变更已由服务端确认，但刷新后的角色与预期不一致；请手动核查。");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toEqual(expectedState);
    const firstRefresh = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "刷新当前角色");
    await act(async () => firstRefresh?.click());

    expect(container.textContent).toContain("管理员变更已由服务端确认，但刷新后的角色与预期不一致；请手动核查。");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toEqual(expectedState);
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent?.includes("平台管理员"))
      .every((button) => button.hasAttribute("disabled"))).toBe(true);

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<IdentityManagementPage account={owner} />));

    expect(container.textContent).toContain("管理员变更已由服务端确认，但刷新后的角色与预期不一致；请手动核查。");
    expect(container.textContent).not.toContain("使用同一请求重试确认");
    expect(JSON.parse(String(sessionStorage.getItem(pendingAdministratorStorageKey)))).toEqual(expectedState);
    const finalRefresh = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "刷新当前角色");
    await act(async () => finalRefresh?.click());

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(container.textContent).toContain("变更已确认，当前角色已刷新。");
    expect(sessionStorage.getItem(pendingAdministratorStorageKey)).toBeNull();
    expect([...articleFor(container, "测试成员").querySelectorAll("button")]
      .some((button) => button.textContent === "撤销平台管理员")).toBe(true);
  });
});
