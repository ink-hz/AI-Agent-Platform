/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

const { requestAuthCode, dingTalkSdk } = vi.hoisted(() => {
  const requestAuthCode = vi.fn();
  return {
    requestAuthCode,
    dingTalkSdk: {
      env: { platform: "android" },
      requestAuthCode,
    },
  };
});

vi.mock("dingtalk-jsapi", () => ({ default: dingTalkSdk }));

import {
  AuthenticationRequired,
  DirectoryUnavailable,
  PermissionDenied,
  changeAdministrator,
  loadAccount,
  inClientLogin,
  inClientLoginAvailable,
  loginReturnPath,
  listManagedUsers,
  platformPath,
  routePrefix,
  type Account,
  type ManagedUser,
} from "./auth";
import { routePath } from "./router";


afterEach(() => {
  vi.useRealTimers();
  window.history.replaceState({}, "", "/");
  dingTalkSdk.env.platform = "android";
  requestAuthCode.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});


function accountResponse(): Response {
  return new Response(JSON.stringify({
    internal_user_id: "owner",
    display_name: "苍渊",
    role: "platform_owner",
    departments: ["项目管理部"],
    gender: "male",
    observation_agent_ids: [],
    directory_freshness: "fresh",
    hard_stale_read_only: false,
    csrf_token: "csrf",
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}


describe("login return path", () => {
  it.each([
    routePath({ name: "admin-fae-session", sessionKey: "fae:session-1" }),
    routePath({ name: "admin-fae-report", reportId: "weekly:2026-08-31" }),
  ])("round-trips a canonical encoded FAE detail path through the login query: %s", (path) => {
    const search = `?${new URLSearchParams({ return_path: path })}`;
    expect(loginReturnPath(search)).toBe(path);
  });

  it.each([
    ["?return_path=/admin/", "/admin/"],
    ["?return_path=%2Fadmin%2F", "/admin/"],
    ["?return_path=%2Foffice%2F", "/office/"],
    ["?return_path=%2Foffice", "/"],
    ["?return_path=%2Foffice%2Fchat", "/"],
    ["?return_path=%2Foffice%2F%3Fview%3Dlodging", "/"],
    ["?return_path=%252Foffice%252F", "/"],
    ["?return_path=%2Foffice%5C", "/"],
    ["?return_path=%2F", "/"],
    ["?return_path=%2Fmissions%2F8c13c965-1b60-472e-b275-199987d1d109", "/missions/8c13c965-1b60-472e-b275-199987d1d109"],
    ["?return_path=%2Fconversations", "/conversations"],
    ["?return_path=%2Fconversations%2F8c13c965-1b60-472e-b275-199987d1d109", "/conversations/8c13c965-1b60-472e-b275-199987d1d109"],
    ["?return_path=%2Fagents%2Fhr-bot", "/agents/hr-bot"],
    ["?return_path=%2Faccount", "/account"],
    ["?return_path=%2Fai-notes", "/ai-notes"],
    ["?return_path=%2Fai-notes%2Fagent-architecture%2Fsystem-handbook", "/ai-notes/agent-architecture/system-handbook"],
    ["?return_path=%2Fadmin%2Fvoc", "/admin/voc"],
    ["?return_path=%2Fadmin%2Ffae", "/admin/fae"],
    ["?return_path=%2Fadmin%2Ffae%2Fsessions%2Ffae%3Asession-1", "/admin/fae/sessions/fae:session-1"],
    ["?return_path=%2Fadmin%2Ffae%2Fissues%2F00000000-0000-0000-0000-000000000001", "/admin/fae/issues/00000000-0000-0000-0000-000000000001"],
    ["?return_path=%2Fadmin%2Ffae%2Freports%2Fweekly%3A2026-08-31", "/admin/fae/reports/weekly:2026-08-31"],
    ["?return_path=%2Fai-notes%2Fa%2F..%2Fadmin", "/"],
    ["?return_path=%2Fai-notes%2FUPPER%2Fhandbook", "/"],
    ["", "/"],
    ["?return_path=https%3A%2F%2Fevil.example%2F", "/"],
    ["?return_path=%2F%2Fevil.example%2F", "/"],
    ["?return_path=%2Fadmin%2F%3Fview%3Dshuttle", "/"],
    ["?return_path=%252Fadmin%252F", "/"],
    ["?return_path=%2Fadmin%5C", "/"],
    ["?return_path=%2Fadmin%2F%00", "/"],
    ["?return_path=%2Fadmin%2F%23fragment", "/"],
    ["?return_path=%2Fconversations%2Fnot-a-uuid", "/"],
    ["?return_path=/admin/#fragment", "/"],
    ["?return_path=%2Fadmin%2F&return_path=%2Fadmin%2F", "/"],
    ["?return_path=%2Fadmin%2F&return_path=%2Faccount", "/"],
  ] as const)("maps %s to %s", (search, expected) => {
    expect(loginReturnPath(search)).toBe(expected);
  });
});


describe("authenticated account bootstrap", () => {
  it("accepts the current employee profile fields returned by the account API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "owner",
      display_name: "苍渊",
      role: "platform_owner",
      departments: ["总经办"],
      gender: "male",
      real_name: "员工姓名",
      mobile: "13800138000",
      primary_department: "总经办",
      observation_agent_ids: [],
      directory_freshness: "fresh",
      hard_stale_read_only: false,
      csrf_token: "csrf",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(loadAccount("")).resolves.toMatchObject({
      internal_user_id: "owner",
      display_name: "苍渊",
      departments: ["总经办"],
    });
  });

  it("accepts and projects trusted DingTalk departments and gender", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(accountResponse()));

    await expect(loadAccount("")).resolves.toMatchObject({
      departments: ["项目管理部"],
      gender: "male",
    });
  });

  it("accepts a nullable trusted gender", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "member",
      display_name: "成员",
      role: "member",
      departments: [],
      gender: null,
      observation_agent_ids: [],
      directory_freshness: "fresh",
      hard_stale_read_only: false,
      csrf_token: "csrf",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(loadAccount("")).resolves.toMatchObject({ departments: [], gender: null });
  });

  it.each([
    [["项目管理部", 7], "male"],
    [["项目管理部"], "unknown"],
  ])("rejects malformed trusted identity fields", async (departments, gender) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "member",
      display_name: "成员",
      role: "member",
      departments,
      gender,
      observation_agent_ids: [],
      directory_freshness: "fresh",
      hard_stale_read_only: false,
      csrf_token: "csrf",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(loadAccount("")).rejects.toThrow("account response invalid");
  });

  it("retries one transient gateway failure", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 502 }))
      .mockResolvedValueOnce(accountResponse());
    vi.stubGlobal("fetch", fetchMock);

    const account = expect(loadAccount("")).resolves.toMatchObject({ display_name: "苍渊" });
    await vi.runAllTimersAsync();

    await account;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("aborts a stalled account read and retries once", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }))
      .mockResolvedValueOnce(accountResponse());
    vi.stubGlobal("fetch", fetchMock);

    const account = loadAccount("");
    await Promise.resolve();
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ signal: expect.any(AbortSignal) }));
    await vi.advanceTimersByTimeAsync(5_200);

    await expect(account).resolves.toMatchObject({ display_name: "苍渊" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry a directory-unavailable response", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    const account = expect(loadAccount("")).rejects.toBeInstanceOf(DirectoryUnavailable);
    await vi.runAllTimersAsync();

    await account;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("accepts the platform administrator role", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "admin",
      display_name: "平台管理员",
      role: "platform_admin",
      departments: ["行政部"],
      gender: "female",
      observation_agent_ids: [],
      directory_freshness: "fresh",
      hard_stale_read_only: false,
      csrf_token: "csrf",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(loadAccount("")).resolves.toMatchObject({ role: "platform_admin" });
  });

  it("rejects an unknown account role", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "unknown",
      display_name: "未知角色",
      role: "platform_superuser",
      departments: [],
      gender: null,
      observation_agent_ids: [],
      directory_freshness: "fresh",
      hard_stale_read_only: false,
      csrf_token: "csrf",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(loadAccount("")).rejects.toThrow("account response invalid");
  });

  it("uses Cookie credentials and the preview prefix without provider identity fields", async () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/account");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "62a31b32-2a92-47d4-9f79-f0c61bca12aa",
      display_name: "苍渊",
      role: "platform_owner",
      departments: ["项目管理部"],
      gender: "male",
      observation_agent_ids: [],
      directory_freshness: "fresh",
      hard_stale_read_only: false,
      csrf_token: "memory-only-csrf",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const account = await loadAccount(routePrefix());

    expect(account.display_name).toBe("苍渊");
    expect(fetchMock).toHaveBeenCalledWith(
      "/_preview/dingtalk-r1/api/v1/account",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(account).not.toHaveProperty("unionid");
    expect(account).not.toHaveProperty("access_token");
    expect(platformPath("/login")).toBe("/_preview/dingtalk-r1/login");
  });

  it.each([
    [401, AuthenticationRequired],
    [403, PermissionDenied],
    [503, DirectoryUnavailable],
  ])("maps HTTP %i to a stable shell state", async (status, ErrorType) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status })));
    await expect(loadAccount("")).rejects.toBeInstanceOf(ErrorType);
  });

  it("rejects extra or malformed account fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "user",
      display_name: "苍渊",
      role: "platform_owner",
      departments: ["项目管理部"],
      gender: "male",
      observation_agent_ids: [],
      directory_freshness: "fresh",
      hard_stale_read_only: false,
      csrf_token: "csrf",
      provider_user_id: "must-not-cross-boundary",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(loadAccount("")).rejects.toThrow("account response invalid");
  });

  it("detects the bundled DingTalk runtime without window.dd", () => {
    delete (window as typeof window & { dd?: unknown }).dd;
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 DingTalk/7.6.50 Android");

    expect(inClientLoginAvailable()).toBe(true);
  });

  it("uses the bundled DingTalk JSAPI code without storing it", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ client_id: "office-client", corp_id: "corp", app_id: "office" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "not-persisted" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 DingTalk/7.6.50 Android");
    requestAuthCode.mockResolvedValueOnce({ code: "one-time-code" });

    await inClientLogin("/office/");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/dingtalk/config?return_path=%2Foffice%2F");
    expect(requestAuthCode).toHaveBeenCalledWith({ clientId: "office-client", corpId: "corp" });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/dingtalk/in-client/exchange");
    expect(fetchMock.mock.calls[1][1]?.body).toBe(JSON.stringify({ code: "one-time-code", app_id: "office" }));
    expect(storage).not.toHaveBeenCalled();
  });

  it("rejects a malformed in-client application response before requesting a code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      client_id: "office-client", corp_id: "corp", app_id: "Office!",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 DingTalk/7.6.50 Android");

    await expect(inClientLogin("/office/")).rejects.toThrow("DingTalk configuration invalid");

    expect(requestAuthCode).not.toHaveBeenCalled();
  });
});


describe("identity management contract", () => {
  const owner: Account = {
    internal_user_id: "owner", display_name: "苍渊", role: "platform_owner",
    departments: ["项目管理部"], gender: "male",
    observation_agent_ids: [], directory_freshness: "fresh",
    hard_stale_read_only: false, csrf_token: "owner-csrf",
  };
  const member: ManagedUser = {
    internal_user_id: "member/id", display_name: "企业成员", role: "member",
    status: "active", scopes: [],
  };

  it.each([
    [false, "POST", "admin_access_approved"],
    [true, "DELETE", "admin_access_revoked"],
  ] as const)("uses the exact administrator API contract for revoke=%s", async (revoke, method, reason) => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await changeAdministrator(owner, {
      targetInternalUserId: member.internal_user_id,
      revoke,
      requestId,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/manage/admins/member%2Fid",
      {
        method,
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": "owner-csrf",
        },
        body: JSON.stringify({ reason, request_id: requestId }),
      },
    );
  });

  it("preserves the backend indeterminate mutation contract for safe replay", async () => {
    const requestId = "47f493ac-e830-4fe7-9e7d-58b1dfcebd56";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "management_mutation_indeterminate",
        request_id: requestId,
      },
    }), { status: 503, headers: { "Content-Type": "application/json" } })));

    await expect(changeAdministrator(owner, {
      targetInternalUserId: member.internal_user_id,
      revoke: false,
      requestId,
    })).rejects.toMatchObject({
      name: "ManagementMutationIndeterminate",
      status: 503,
      requestId,
    });
  });

  it("fails closed when the managed-user list contains an unknown role", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [{
      internal_user_id: "unknown", display_name: "未知角色", status: "active",
      role: "platform_superuser", scopes: [],
    }] }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(listManagedUsers()).rejects.toThrow("management response invalid");
  });
});
