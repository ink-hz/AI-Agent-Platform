/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { inClientLogin } from "./auth";

const { requestAuthCode } = vi.hoisted(() => ({ requestAuthCode: vi.fn() }));
vi.mock("dingtalk-jsapi", () => ({ default: { requestAuthCode } }));

beforeEach(() => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("DingTalk/7.6.50 Android");
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  requestAuthCode.mockReset();
  window.history.replaceState({}, "", "/");
});

describe.each(["/", "/_preview/dingtalk-r1/"])("in-client login under %s", (prefix) => {
  it.each([
    ["/manufacturing", "platform"],
    ["/office/", "office"],
    ["/voc/", "voc"],
    ["/", "platform"],
  ] as const)("obtains a challenge before requesting the %s application's one-time code", async (returnPath, appId) => {
    window.history.replaceState({}, "", prefix + "login");
    const events: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      events.push(path);
      expect(init?.credentials).toBe("include");
      if (path === prefix + "login") {
        expect(init?.cache).toBe("no-store");
        return new Response("<html/>");
      }
      if (path.startsWith(prefix + "api/v1/auth/dingtalk/config?")) {
        expect(new URL(path, location.origin).searchParams.get("return_path")).toBe(prefix + returnPath.slice(1));
        return Response.json({ client_id: appId + "-client", corp_id: "corp", app_id: appId });
      }
      if (path === prefix + "api/v1/account") {
        return Response.json({ csrf_token: "session-csrf" });
      }
      expect(path).toBe(prefix + "api/v1/auth/dingtalk/in-client/exchange");
      expect(init?.method).toBe("POST");
      expect(init?.headers).toMatchObject({ "X-CSRF-Token": "session-csrf" });
      expect(JSON.parse(String(init?.body))).toEqual({ code: "one-time-code", app_id: appId });
      return Response.json({});
    });
    requestAuthCode.mockImplementationOnce(async (options) => {
      events.push("requestAuthCode");
      expect(options).toEqual({ clientId: appId + "-client", corpId: "corp" });
      return { code: "one-time-code" };
    });
    vi.stubGlobal("fetch", fetcher);

    await inClientLogin(returnPath);

    expect(events).toEqual([
      prefix + "login",
      prefix + "api/v1/auth/dingtalk/config?" + new URLSearchParams({ return_path: prefix + returnPath.slice(1) }),
      "requestAuthCode",
      prefix + "api/v1/account",
      prefix + "api/v1/auth/dingtalk/in-client/exchange",
    ]);
    expect(requestAuthCode).toHaveBeenCalledTimes(1);
  });

  it.each([403, 503, "network"])("does not request a code or exchange when challenge initialization fails: %s", async (failure) => {
    window.history.replaceState({}, "", prefix + "login");
    const fetcher = vi.fn().mockImplementation(async () => {
      if (failure === "network") throw new TypeError("offline");
      return new Response("{}", { status: Number(failure) });
    });
    vi.stubGlobal("fetch", fetcher);

    await expect(inClientLogin("/manufacturing")).rejects.toThrow();

    expect(fetcher).toHaveBeenCalledExactlyOnceWith(prefix + "login", {
      credentials: "include", cache: "no-store",
    });
    expect(requestAuthCode).not.toHaveBeenCalled();
  });

  it("waits for the challenge response before configuration or SDK authorization", async () => {
    window.history.replaceState({}, "", prefix + "login");
    let finish!: (value: Response) => void;
    const fetcher = vi.fn((_input: RequestInfo | URL) => new Promise<Response>((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const login = inClientLogin("/manufacturing");
    const outcome = login.then(() => undefined, (error: unknown) => error);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    try {
      expect(fetcher.mock.calls[0][0]).toBe(prefix + "login");
      expect(requestAuthCode).not.toHaveBeenCalled();
    } finally {
      finish(new Response("{}", { status: 503 }));
      expect(await outcome).toBeInstanceOf(Error);
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("renews the challenge on a second attempt after ten minutes on the same page", async () => {
    window.history.replaceState({}, "", prefix + "login");
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-16T09:00:00Z"));
    let expiresAt = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === prefix + "login") {
        expiresAt = Date.now() + 600_000;
        return new Response("<html/>");
      }
      if (path.includes("/config?")) return Response.json({ client_id: "platform-client", corp_id: "corp", app_id: "platform" });
      if (path.endsWith("/account")) return new Response("{}", { status: 401 });
      expect(path).toBe(prefix + "api/v1/auth/dingtalk/in-client/exchange");
      return new Response("{}", { status: Date.now() < expiresAt ? 200 : 503 });
    });
    requestAuthCode.mockResolvedValue({ code: "one-time-code" });
    vi.stubGlobal("fetch", fetcher);

    await inClientLogin("/manufacturing");
    vi.setSystemTime(Date.now() + 601_000);
    await inClientLogin("/manufacturing");

    expect(fetcher.mock.calls.filter(([input]) => input === prefix + "login")).toHaveLength(2);
    expect(requestAuthCode).toHaveBeenCalledTimes(2);
  });
});
