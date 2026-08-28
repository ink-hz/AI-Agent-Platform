/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkroomAction } from "../../workroomTypes";
import { ActionCard } from "./ActionCard";


const pendingAction: WorkroomAction = {
  actionId: "action-1",
  taskId: "task-1",
  actionKind: "voc.submit_draft",
  status: "pending",
  executionStatus: "not_started",
  summary: "提交本次 VOC 草稿",
  impact: "确认后会把当前草稿提交到 VOC 复审流程。",
  actionDigest: "a".repeat(64),
  expiresAt: "2026-08-28T12:00:00Z",
  confirmedAt: null,
  confirmedBy: null,
};


describe("ActionCard", () => {
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
  });

  it("renders a pending action from the server projection and confirms its exact digest", async () => {
    const confirm = vi.fn().mockResolvedValue({
      ...pendingAction,
      status: "confirmed",
      executionStatus: "queued",
      confirmedAt: "2026-08-28T10:01:00Z",
      confirmedBy: "苍渊",
    } satisfies WorkroomAction);
    const reject = vi.fn();
    await act(async () => root.render(<ActionCard
      action={pendingAction}
      onConfirm={confirm}
      onReject={reject}
    />));

    expect(container.textContent).toContain("提交本次 VOC 草稿");
    expect(container.textContent).toContain("确认后会把当前草稿提交到 VOC 复审流程。");
    expect(container.textContent).not.toContain(pendingAction.taskId);
    expect(container.textContent).not.toContain(pendingAction.actionDigest);
    const button = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((item) => item.textContent === "确认执行");
    await act(async () => button?.click());

    expect(confirm).toHaveBeenCalledWith(pendingAction.actionId, pendingAction.actionDigest);
    expect(reject).not.toHaveBeenCalled();
    expect(container.textContent).toContain("已由苍渊确认");
    expect(container.textContent).toContain("等待执行");

    await act(async () => root.render(<ActionCard
      action={{ ...pendingAction }}
      onConfirm={confirm}
      onReject={reject}
    />));
    expect(container.textContent).toContain("等待执行");
    expect(container.textContent).not.toContain("确认前不会执行");
  });

  it("rejects a pending action without sending its parameters through the browser", async () => {
    const confirm = vi.fn();
    const reject = vi.fn().mockResolvedValue({ ...pendingAction, status: "rejected" } satisfies WorkroomAction);
    await act(async () => root.render(<ActionCard
      action={pendingAction}
      onConfirm={confirm}
      onReject={reject}
    />));

    const button = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((item) => item.textContent === "拒绝");
    await act(async () => button?.click());

    expect(reject).toHaveBeenCalledWith(pendingAction.actionId);
    expect(confirm).not.toHaveBeenCalled();
    expect(container.textContent).toContain("已拒绝");
  });

  it("shows an explicit warning when a server action is superseded", async () => {
    await act(async () => root.render(<ActionCard
      action={{ ...pendingAction, status: "superseded" }}
      onConfirm={vi.fn()}
      onReject={vi.fn()}
    />));

    expect(container.textContent).toContain("该操作已被更新，不能再执行");
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("keeps a failed mutation explicit and retryable", async () => {
    const confirm = vi.fn().mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<ActionCard
      action={pendingAction}
      onConfirm={confirm}
      onReject={vi.fn()}
    />));

    const button = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((item) => item.textContent === "确认执行");
    await act(async () => button?.click());

    expect(container.textContent).toContain("操作暂未提交，请重试");
    expect(button?.disabled).toBe(false);
  });
});
