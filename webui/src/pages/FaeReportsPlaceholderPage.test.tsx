/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";


afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});


describe("FAE reports placeholder routes", () => {
  it.each([
    "/admin/fae/reports",
    "/admin/fae/reports/weekly%3A2026-08-31",
  ])("renders the truthful reports placeholder at %s", async (path) => {
    window.history.replaceState({}, "", path);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("deployment unavailable")));
    const container = document.createElement("div"); document.body.append(container);
    const root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => root.render(<App />));

    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      "分析报告尚未接入Sessions 与问题治理可以正常使用；这里不会用演示数据代替 FAE 的真实分析结果。",
    );
    expect(container.textContent).not.toMatch(/fixture|sample|mock/i);
    await act(async () => root.unmount()); container.remove();
  });
});
