/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { MissionEvent } from "../../brainTypes";
import { MissionTimeline } from "./MissionTimeline";


function event(seq: number, event_type: string, payload: Record<string, unknown>): MissionEvent {
  return {
    event_id: `event-${seq}`, mission_id: "mission", run_id: seq > 1 ? "run" : null,
    seq, event_type, payload, created_at: `2026-08-22T10:00:0${seq}Z`,
  };
}


describe("MissionTimeline", () => {
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

  it("renders persisted collaboration stages in sequence", async () => {
    await act(async () => root.render(<MissionTimeline directAgentId={null} events={[
      event(5, "mission.completed", { text: "# 最终交付" }),
      event(1, "brain.responding", { text: "正在分析需求" }),
      event(2, "task.dispatched", { text: "交给 HR Agent", agent_id: "hr-bot" }),
      event(3, "agent.progress", { text: "正在定位候选人", progress: 0.5 }),
      event(4, "agent.result", { text: "## 专业结果\n- 候选人 A", agent_id: "hr-bot" }),
    ]} missionMode="brain" />));

    const titles = [...container.querySelectorAll(".mission-event-title")].map((node) => node.textContent);
    expect(titles).toEqual(["分析需求", "已交付专业 Agent", "执行进度", "专业结果", "最终交付"]);
    expect(container.querySelector("h1")?.textContent).toBe("最终交付");
    expect(container.textContent).toContain("候选人 A");
  });

  it("renders Markdown without activating raw HTML", async () => {
    await act(async () => root.render(<MissionTimeline directAgentId={null} events={[
      event(1, "mission.completed", {
        text: "# 安全结果\n<script>window.__owned=true</script><img src=x onerror=window.__owned=true>",
      }),
    ]} missionMode="brain" />));

    expect(container.querySelector("h1")?.textContent).toBe("安全结果");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect((window as typeof window & { __owned?: boolean }).__owned).toBeUndefined();
  });

  it("shows persisted failure and interruption states explicitly", async () => {
    await act(async () => root.render(<MissionTimeline directAgentId={null} events={[
      event(1, "mission.failed", { text: "专业 Agent 执行失败", reason_code: "professional_failed" }),
      event(2, "mission.interrupted", { text: "专业 Agent 执行通道离线", reason_code: "execution_interrupted" }),
    ]} missionMode="brain" />));

    expect(container.querySelectorAll(".mission-event.is-failure")).toHaveLength(2);
    expect(container.textContent).toContain("专业 Agent 执行通道离线");
  });

  it("shows the real delegated objective and exposes live progress semantics", async () => {
    await act(async () => root.render(<MissionTimeline directAgentId={null} events={[
      event(1, "plan.created", {
        text: "已选择一个专业 Agent", selected_agent_id: "hr-bot",
        objective: "从 GitHub 定位视觉算法候选人", rationale_summary: "HR Agent 能完成专业社区搜寻",
      }),
      event(2, "task.dispatched", { text: "任务已交给 HR Agent", agent_id: "hr-bot" }),
      event(3, "agent.progress", { text: "搜索中", progress: 0.5 }),
    ]} missionMode="brain" />));

    expect(container.textContent).toContain("从 GitHub 定位视觉算法候选人");
    expect(container.textContent).toContain("HR Agent 能完成专业社区搜寻");
    expect(container.querySelector("ol")?.getAttribute("aria-live")).toBe("polite");
    const progress = container.querySelector("[role=progressbar]");
    expect(progress?.getAttribute("aria-valuenow")).toBe("50");
    expect(progress?.getAttribute("aria-valuemin")).toBe("0");
    expect(progress?.getAttribute("aria-valuemax")).toBe("100");
  });

  it.each([
    ["mission.completed", "最终交付"],
    ["mission.failed", "任务未完成"],
    ["mission.interrupted", "执行已中断"],
    ["mission.cancelled", "任务已停止"],
  ])("attributes direct-agent %s events to the selected professional Agent", async (eventType, title) => {
    await act(async () => root.render(<MissionTimeline
      directAgentId="hr-bot"
      events={[event(1, eventType, { text: "终态内容" })]}
      missionMode="direct_agent"
    />));

    const card = container.querySelector(".mission-event");
    expect(card?.querySelector("header span")?.textContent).toBe("专业 Agent · hr-bot");
    expect(card?.querySelector(".mission-event-title")?.textContent).toBe(title);
  });

  it("attributes direct execution to its Agent while keeping brain stages with Agent 大脑", async () => {
    await act(async () => root.render(<MissionTimeline
      directAgentId="hr-bot"
      events={[
        event(1, "brain.responding", { text: "分析需求" }),
        event(2, "agent.progress", { text: "执行中", progress: 0.5 }),
        event(3, "synthesis.started", { text: "整理交付" }),
      ]}
      missionMode="direct_agent"
    />));

    const actors = [...container.querySelectorAll(".mission-event header span")]
      .map((node) => node.textContent);
    expect(actors).toEqual(["Agent 大脑", "专业 Agent · hr-bot", "Agent 大脑"]);
  });

  it("keeps delegated Mission completion attributed to Agent 大脑", async () => {
    await act(async () => root.render(<MissionTimeline
      directAgentId={null}
      events={[event(1, "mission.completed", { text: "综合交付" })]}
      missionMode="brain"
    />));

    expect(container.querySelector(".mission-event header span")?.textContent).toBe("Agent 大脑");
  });
});
