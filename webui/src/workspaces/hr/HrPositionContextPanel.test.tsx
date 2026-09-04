/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrPositionContextPanel } from "./HrPositionContextPanel";

const positionId = "00000000-0000-4000-8000-000000000001";
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

it("keeps module selection after a baseline conflict", async () => {
  const api = { context: vi.fn().mockResolvedValue({ current: null, history: [], drafts: [{ contextVersionId: "00000000-0000-4000-8000-000000000002", displayVersion: 2, status: "draft", summary: "新画像", modules: { profile: "技术负责人" }, rowVersion: 1, createdAt: "2026-09-04T00:00:00Z" }] }), confirmContext: vi.fn().mockRejectedValue({ status: 409 }) };
  await act(async () => root.render(<HrPositionContextPanel api={api as never} positionId={positionId} />));
  await act(async () => container.querySelector<HTMLButtonElement>("button")?.click());
  expect(container.textContent).toContain("基线已变化");
  expect(container.textContent).toContain("技术负责人");
});
