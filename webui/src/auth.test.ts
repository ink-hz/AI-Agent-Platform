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
  listManagedUsers,
  platformPath,
  routePrefix,
  type Account,
  type ManagedUser,
} from "./auth";


afterEach(() => {
  window.history.replaceState({}, "", "/");
  dingTalkSdk.env.platform = "android";
  requestAuthCode.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});


describe("authenticated account bootstrap", () => {
  it("accepts the platform administrator role", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      internal_user_id: "admin",
      display_name: "平台管理员",
      role: "platform_admin",
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
      .mockResolvedValueOnce(new Response(JSON.stringify({ client_id: "client", corp_id: "corp" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "not-persisted" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 DingTalk/7.6.50 Android");
    requestAuthCode.mockResolvedValueOnce({ code: "one-time-code" });

    await inClientLogin();

    expect(requestAuthCode).toHaveBeenCalledWith({ clientId: "client", corpId: "corp" });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/dingtalk/in-client/exchange");
    expect(fetchMock.mock.calls[1][1]?.body).toBe(JSON.stringify({ code: "one-time-code" }));
    expect(storage).not.toHaveBeenCalled();
  });
});


describe("identity management contract", () => {
  const owner: Account = {
    internal_user_id: "owner", display_name: "苍渊", role: "platform_owner",
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
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await changeAdministrator(owner, member, revoke);

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
        body: JSON.stringify({ reason }),
      },
    );
  });

  it("fails closed when the managed-user list contains an unknown role", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [{
      internal_user_id: "unknown", display_name: "未知角色", status: "active",
      role: "platform_superuser", scopes: [],
    }] }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(listManagedUsers()).rejects.toThrow("management response invalid");
  });
});
