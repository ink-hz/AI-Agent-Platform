/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";


function BrokenWorkspace(): never {
  throw new Error("workspace render failed");
}


describe("WorkspaceErrorBoundary", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    window.history.replaceState({}, "", "/marketing/inbound");
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("contains a render failure inside the current workspace", async () => {
    await act(async () => root.render(
      <WorkspaceErrorBoundary title="Marketing Agent"><BrokenWorkspace /></WorkspaceErrorBoundary>,
    ));

    expect(window.location.pathname).toBe("/marketing/inbound");
    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain("Marketing Agent 暂时不可用");
    expect(container.textContent).toContain("当前工作区加载失败，其他 Agent 不受影响。");
    expect(container.querySelector('a[href^="/admin"]')).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("button")?.textContent).toBe("重试");
  });
});
