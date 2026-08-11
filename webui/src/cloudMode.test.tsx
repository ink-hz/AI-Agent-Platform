/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";


let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
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


describe("cloud replica mode", () => {
  it("shows a compact read-only banner and hides Review navigation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "cloud-replica", read_only: true, auth: "ssh-tunnel",
      freshness: "current", last_success_at: "2026-08-11T08:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "overview" }}><p>内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("云端脱敏只读副本");
    expect(container.textContent).toContain("数据已同步");
    expect(container.textContent).not.toContain("复审闭环");
    expect(container.querySelector(".cloud-replica-banner")?.className).toContain("is-current");
  });

  it("visibly distinguishes stale replica data", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      mode: "cloud-replica", read_only: true, auth: "ssh-tunnel",
      freshness: "stale", last_success_at: "2026-08-11T07:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await act(async () => root.render(
      <AppShell route={{ name: "sessions" }}><p>内容</p></AppShell>,
    ));
    await act(async () => await Promise.resolve());

    expect(container.textContent).toContain("数据已过期");
    expect(container.querySelector(".cloud-replica-banner")?.className).toContain("is-stale");
  });
});
