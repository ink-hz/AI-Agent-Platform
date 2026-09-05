/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrPositionPackage } from "../../hrTypes";
import { HrConversationOutcomePanel } from "./HrConversationOutcomePanel";


const conversationId = "33333333-3333-4333-8333-333333333333";
const positionId = "44444444-4444-4444-8444-444444444444";
const positionPackage = {
  draftId: "11111111-1111-4111-8111-111111111111",
  draftVersionId: "22222222-2222-4222-8222-222222222222",
  conversationId,
  versionNumber: 2,
  title: "视觉算法工程师",
  modules: {
    mission: { text: "旧岗位需求" }, jd: { text: "旧 JD" }, jr: { text: "旧 JR" },
  },
  rowVersion: 3,
  createdAt: "2026-09-04T01:00:00Z",
  updatedAt: "2026-09-04T02:00:00Z",
} satisfies HrPositionPackage;


describe("HrConversationOutcomePanel", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders no card when the conversation is still a clarification turn", async () => {
    const api = {
      positionPackage: vi.fn().mockRejectedValue({ status: 404 }),
      confirmPositionPackage: vi.fn(),
    };
    await act(async () => root.render(<HrConversationOutcomePanel
      api={api as never} conversationId={conversationId} csrfToken="csrf"
    />));

    expect(api.positionPackage).toHaveBeenCalledWith(conversationId, expect.any(AbortSignal));
    expect(container.querySelector('[aria-label="岗位方案"]')).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("discovers a package completed after the conversation page first returned 404", async () => {
    vi.useFakeTimers();
    const api = {
      positionPackage: vi.fn().mockRejectedValueOnce({ status: 404 }).mockResolvedValueOnce(positionPackage),
      confirmPositionPackage: vi.fn(),
    };
    await act(async () => root.render(<HrConversationOutcomePanel
      api={api as never} conversationId={conversationId} csrfToken="csrf"
    />));
    expect(container.textContent).toBe("");

    await act(async () => vi.advanceTimersByTimeAsync(5_000));

    expect(api.positionPackage).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[aria-label="岗位方案"]')).not.toBeNull();
  });

  it("bounds and backs off package discovery for a clarification-only conversation", async () => {
    vi.useFakeTimers();
    const api = {
      positionPackage: vi.fn().mockRejectedValue({ status: 404 }),
      confirmPositionPackage: vi.fn(),
    };
    await act(async () => root.render(<HrConversationOutcomePanel
      api={api as never} conversationId={conversationId} csrfToken="csrf"
    />));

    for (let step = 0; step < 20; step += 1) {
      await act(async () => vi.advanceTimersByTimeAsync(30_000));
    }
    const callsAfterWindow = api.positionPackage.mock.calls.length;
    for (let step = 0; step < 5; step += 1) {
      await act(async () => vi.advanceTimersByTimeAsync(30_000));
    }

    expect(callsAfterWindow).toBeGreaterThan(1);
    expect(callsAfterWindow).toBeLessThanOrEqual(13);
    expect(api.positionPackage).toHaveBeenCalledTimes(callsAfterWindow);
    expect(container.textContent).toBe("");
  });

  it("refreshes a conflicted package before enabling an explicit retry, then uses SPA navigation", async () => {
    const refreshed = {
      ...positionPackage,
      draftVersionId: "55555555-5555-4555-8555-555555555555",
      modules: { ...positionPackage.modules, mission: { text: "其他人更新后的岗位需求" } },
      rowVersion: 4,
      versionNumber: 3,
    };
    const api = {
      positionPackage: vi.fn().mockResolvedValueOnce(positionPackage).mockResolvedValueOnce(refreshed),
      confirmPositionPackage: vi.fn().mockRejectedValueOnce({ status: 409 }).mockResolvedValueOnce({
        positionId, contextVersionId: "66666666-6666-4666-8666-666666666666", conversationId,
      }),
    };
    const navigate = vi.fn();
    const onConfirmed = vi.fn();
    await act(async () => root.render(<HrConversationOutcomePanel
      api={api as never} conversationId={conversationId} csrfToken="csrf"
      onConfirmed={onConfirmed} onNavigate={navigate}
    />));
    const confirm = () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认并加入岗位库")!;

    await act(async () => confirm().click());

    expect(api.positionPackage).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("其他人更新后的岗位需求");
    expect(container.textContent).toContain("岗位方案已更新，请核对后重试");
    expect(navigate).not.toHaveBeenCalled();

    await act(async () => confirm().click());

    expect(api.confirmPositionPackage).toHaveBeenNthCalledWith(
      2, refreshed.draftId, refreshed.draftVersionId, refreshed.rowVersion,
      expect.any(String), expect.any(AbortSignal),
    );
    expect(onConfirmed).toHaveBeenCalledWith(expect.objectContaining({ positionId, conversationId }), refreshed);
    expect(navigate).toHaveBeenCalledWith(`/hr/positions/${positionId}/conversations/${conversationId}`);
  });

  it("blocks stale confirmation when the 409 refresh fails until a fresh package loads", async () => {
    const refreshed = { ...positionPackage, rowVersion: 4, versionNumber: 3 };
    const api = {
      positionPackage: vi.fn()
        .mockResolvedValueOnce(positionPackage)
        .mockRejectedValueOnce(new Error("refresh offline"))
        .mockResolvedValueOnce(refreshed),
      confirmPositionPackage: vi.fn().mockRejectedValue({ status: 409 }),
    };
    await act(async () => root.render(<HrConversationOutcomePanel
      api={api as never} conversationId={conversationId} csrfToken="csrf"
    />));
    const confirmation = () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认并加入岗位库")!;

    await act(async () => confirmation().click());

    expect(confirmation().disabled).toBe(true);
    const refresh = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "重新读取最新方案")!;
    expect(refresh).toBeDefined();
    await act(async () => refresh.click());
    expect(container.textContent).toContain("V3");
    expect(confirmation().disabled).toBe(false);
  });

  it("does not confirm a position package for a hard-stale read-only account", async () => {
    const api = {
      positionPackage: vi.fn().mockResolvedValue(positionPackage),
      confirmPositionPackage: vi.fn(),
    };
    await act(async () => root.render(<HrConversationOutcomePanel
      api={api as never} conversationId={conversationId} csrfToken="csrf" readOnly
    />));

    const confirmation = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认并加入岗位库")!;
    expect(confirmation.disabled).toBe(true);
    await act(async () => confirmation.click());
    expect(api.confirmPositionPackage).not.toHaveBeenCalled();
  });
});
