/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "./LoginPage";


describe("LoginPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    window.history.replaceState({}, "", "/login");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("offers DingTalk QR login without a password form or browser token storage", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const onStartQr = vi.fn().mockResolvedValue("https://login.dingtalk.com/oauth2/auth");
    await act(async () => root.render(<LoginPage onStartQr={onStartQr} onNavigate={() => undefined} />));

    expect(container.textContent).toContain("钉钉扫码登录");
    expect(container.querySelector("input[type=password]")).toBeNull();
    const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes("扫码登录"));
    await act(async () => button?.click());
    expect(onStartQr).toHaveBeenCalledWith("/account");
    expect(storage).not.toHaveBeenCalled();
  });

  it("uses the exact validated admin return for QR login", async () => {
    window.history.replaceState({}, "", "/login?return_path=%2Fadmin%2F");
    const onStartQr = vi.fn().mockResolvedValue("https://login.dingtalk.com/oauth2/auth");
    await act(async () => root.render(<LoginPage onStartQr={onStartQr} onNavigate={() => undefined} />));

    const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes("扫码登录"));
    await act(async () => button?.click());

    expect(onStartQr).toHaveBeenCalledWith("/admin/");
  });

  it("never forwards an unvalidated query to QR login", async () => {
    window.history.replaceState({}, "", "/login?return_path=https%3A%2F%2Fevil.example%2F");
    const onStartQr = vi.fn().mockResolvedValue("https://login.dingtalk.com/oauth2/auth");
    await act(async () => root.render(<LoginPage onStartQr={onStartQr} onNavigate={() => undefined} />));
    window.history.replaceState({}, "", "/login?return_path=%2Fadmin%2F");

    const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes("扫码登录"));
    await act(async () => button?.click());

    expect(onStartQr).toHaveBeenCalledWith("/account");
  });

  it("renders only a generic callback failure", async () => {
    window.history.replaceState({}, "", "/login?error=provider-secret-value");
    await act(async () => root.render(<LoginPage onStartQr={vi.fn()} onNavigate={() => undefined} />));
    expect(container.textContent).toContain("登录未完成");
    expect(container.textContent).not.toContain("provider-secret-value");
  });

  it("automatically performs in-client免登 once", async () => {
    const onInClient = vi.fn().mockResolvedValue(undefined);
    const onNavigate = vi.fn();
    await act(async () => root.render(<LoginPage onStartQr={vi.fn()} onInClient={onInClient} onNavigate={onNavigate} />));

    expect(onInClient).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith("/account");
  });

  it("navigates an in-client login to the exact validated admin return", async () => {
    window.history.replaceState({}, "", "/login?return_path=%2Fadmin%2F");
    const onInClient = vi.fn().mockResolvedValue(undefined);
    const onNavigate = vi.fn();
    await act(async () => root.render(<LoginPage onStartQr={vi.fn()} onInClient={onInClient} onNavigate={onNavigate} />));

    expect(onInClient).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith("/admin/");
  });

  it("keeps manual in-client retry after automatic failure", async () => {
    const onInClient = vi.fn()
      .mockRejectedValueOnce(new Error("bridge unavailable"))
      .mockResolvedValueOnce(undefined);
    await act(async () => root.render(<LoginPage onStartQr={vi.fn()} onInClient={onInClient} onNavigate={() => undefined} />));

    expect(container.textContent).toContain("登录未完成");
    const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes("钉钉内免登"));
    await act(async () => button?.click());
    expect(onInClient).toHaveBeenCalledTimes(2);
  });
});
