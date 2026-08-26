/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { WorkroomTurn } from "../../workroomTypes";
import { MultiAgentWorkroom } from "./MultiAgentWorkroom";


function fixture(status: WorkroomTurn["status"] = "running"): WorkroomTurn {
  return {
    turnId: "turn-1",
    status,
    defaultExpanded: status === "running",
    tasks: [
      {
        taskId: "task-1", childSessionId: "child-1", agentId: "hr-bot", agentLabel: "HR Agent",
        objective: "定位复合人才", publicReason: "需要专业人才判断", status: "running",
        lastUpdate: "找到两类候选人", artifactCount: 1,
      },
    ],
    timeline: [
      {
        eventId: "event-1", taskId: "task-1", seq: 1, sourceKind: "agent_work",
        sourceLabel: "HR Agent · 工作进展", text: "找到两类候选人",
        createdAt: "2026-08-26T02:00:01Z", interrupted: false,
      },
    ],
    deliverables: [
      { eventId: "event-2", taskId: "task-1", attachmentRef: "attachment-1", label: "人才地图" },
    ],
  };
}


describe("MultiAgentWorkroom", () => {
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

  it("opens an active real workroom and exposes accessible tabs", async () => {
    await act(async () => root.render(<MultiAgentWorkroom workroom={fixture()} />));

    const details = container.querySelector("details");
    expect(details?.open).toBe(true);
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.getAttribute("aria-label")).toBe("团队");
    const timeline = [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.getAttribute("aria-label") === "协作记录");
    await act(async () => timeline?.click());
    expect(timeline?.getAttribute("aria-selected")).toBe("true");
    expect(container.textContent).toContain("找到两类候选人");
  });

  it("collapses a completed workroom and opens a read-only child session in place", async () => {
    const completed = fixture("completed");
    completed.tasks[0] = { ...completed.tasks[0], status: "completed" };
    completed.defaultExpanded = false;
    await act(async () => root.render(<MultiAgentWorkroom workroom={completed} />));

    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    await act(async () => {
      details!.open = true;
      details!.dispatchEvent(new Event("toggle", { bubbles: true }));
    });
    const card = container.querySelector<HTMLButtonElement>('button[aria-label="查看 HR Agent 子会话"]');
    await act(async () => card?.click());
    expect(container.textContent).toContain("HR Agent 子会话");
    expect(container.textContent).toContain("只读记录");
  });

  it("links deliverables through the Platform attachment endpoint", async () => {
    await act(async () => root.render(<MultiAgentWorkroom workroom={fixture()} />));
    const deliverables = [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.getAttribute("aria-label") === "交付成果");
    await act(async () => deliverables?.click());

    expect(container.querySelector<HTMLAnchorElement>("a")?.getAttribute("href"))
      .toBe("/api/v1/attachments/attachment-1");
  });
});
