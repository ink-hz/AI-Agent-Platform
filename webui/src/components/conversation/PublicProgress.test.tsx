/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ConversationEvent } from "../../conversationTypes";
import { PublicProgress } from "./PublicProgress";


function event(
  seq: number,
  eventType: string,
  payload: Record<string, unknown>,
): ConversationEvent {
  return {
    event_id: `event-${seq}`,
    conversation_id: "conversation",
    seq,
    turn_id: "turn",
    event_type: eventType,
    payload,
    created_at: `2026-08-25T10:00:0${seq}Z`,
  };
}


describe("PublicProgress", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("renders nothing diagnostic after a direct Agent turn completes", async () => {
    await act(async () => root.render(<PublicProgress
      active={false}
      assistantLabel="HR Agent"
      events={[event(1, "turn.completed", { status: "completed" })]}
      mode="direct_agent"
      stopButton={null}
    />));

    expect(container.textContent).toBe("");
    expect(container.textContent).not.toContain("completed");
    expect(container.textContent).not.toContain("执行过程");
  });

  it("shows an honest queued state before the first direct Agent event", async () => {
    await act(async () => root.render(<PublicProgress
      active
      assistantLabel="HR Agent"
      events={[]}
      mode="direct_agent"
      stopButton={<button type="button">停止</button>}
    />));

    expect(container.textContent).toContain("已进入队列");
    expect(container.textContent).not.toContain("50%");
  });

  it("shows real direct Agent updates in event order without duplicate summaries", async () => {
    await act(async () => root.render(<PublicProgress
      active
      assistantLabel="HR Agent"
      events={[
        event(4, "agent.work_update", { summary: "正在整理岗位要求" }),
        event(1, "agent.task_dispatched", { summary: "任务已进入执行队列" }),
        event(3, "agent.task_progress", { summary: "正在读取附件" }),
        { ...event(5, "agent.task_progress", { summary: "正在读取附件" }), event_id: "event-5" },
        event(2, "agent.task_accepted", { text: "HR Agent 已开始执行" }),
      ]}
      mode="direct_agent"
      stopButton={<button type="button">停止</button>}
    />));

    const updates = [...container.querySelectorAll("li")].map((node) => node.textContent);
    expect(updates).toEqual([
      "任务已进入执行队列",
      "HR Agent 已开始执行",
      "正在读取附件",
      "正在整理岗位要求",
    ]);
    expect(container.textContent?.match(/正在读取附件/g)).toHaveLength(1);
  });

  it("shows only a factual live status while the workroom owns collaboration detail", async () => {
    await act(async () => root.render(<PublicProgress
      active
      assistantLabel="Agent 大脑"
      events={[
        event(1, "brain.started", { status: "running" }),
        event(2, "agent.task_dispatched", { agent_name: "HR Agent", public_reason: "需要人才判断" }),
        event(3, "agent.task_completed", { agent_name: "HR Agent", status: "completed" }),
        event(4, "brain.resumed", { status: "running" }),
      ]}
      mode="brain"
      stopButton={<button type="button">停止</button>}
    />));

    expect(container.textContent).toContain("本轮仍在执行，你可以继续补充要求");
    expect(container.textContent).not.toContain("查看协作过程");
    expect(container.textContent).not.toContain("正在整合结果");
    expect(container.textContent).not.toContain("正在分析需求");
    expect(container.textContent).not.toContain("completed");
  });
});
