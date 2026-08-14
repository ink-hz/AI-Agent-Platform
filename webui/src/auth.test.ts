/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AuthenticationRequired,
  DirectoryUnavailable,
  PermissionDenied,
  loadAccount,
  inClientLogin,
  platformPath,
  routePrefix,
} from "./auth";


afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});


describe("authenticated account bootstrap", () => {
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

  it("uses the injected DingTalk JSAPI code without storing it", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ client_id: "client", corp_id: "corp" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "not-persisted" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    Object.assign(window, { dd: { requestAuthCode: vi.fn().mockResolvedValue({ code: "one-time-code" }) } });

    await inClientLogin();

    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/dingtalk/in-client/exchange");
    expect(fetchMock.mock.calls[1][1]?.body).toBe(JSON.stringify({ code: "one-time-code" }));
    expect(storage).not.toHaveBeenCalled();
    delete (window as typeof window & { dd?: unknown }).dd;
  });
});
