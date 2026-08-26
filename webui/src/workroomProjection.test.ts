import { describe, expect, it } from "vitest";

import type { ConversationEvent } from "./conversationTypes";
import { projectWorkroom } from "./workroomProjection";


function event(
  seq: number,
  eventType: string,
  payload: Record<string, unknown> = {},
  eventId = `event-${seq}`,
): ConversationEvent {
  return {
    event_id: eventId,
    conversation_id: "conversation-1",
    seq,
    turn_id: "turn-1",
    event_type: eventType,
    payload,
    created_at: `2026-08-26T02:00:${String(seq).padStart(2, "0")}Z`,
  };
}


const task = {
  task_id: "task-1",
  child_session_id: "child-1",
  agent_id: "hr-bot",
  objective_summary: "定位视觉与硬件复合人才",
  public_reason: "需要专业人才搜索判断",
  status: "running",
};


describe("projectWorkroom", () => {
  it("creates no workroom without a real delegated task", () => {
    expect(projectWorkroom([
      event(1, "brain.thinking_summary", {
        source: "provider", source_ref: "brain-run", summary: "正在判断是否需要委派",
      }),
    ])).toBeNull();
  });

  it("keeps every genuine source distinct and ordered", () => {
    const result = projectWorkroom([
      event(6, "agent.message", { ...task, source: "provider", source_ref: "agent-run", summary: "建议扩大公司范围" }),
      event(2, "agent.task_dispatched", task),
      event(1, "brain.thinking_summary", { source: "provider", source_ref: "brain-run", summary: "需要人才 Agent" }),
      event(3, "brain.agent_message_sent", { ...task, summary: "先定位三类经历组合" }),
      event(4, "agent.thinking_summary", { ...task, source: "provider", source_ref: "agent-run", summary: "正在交叉核对经历" }),
      event(5, "agent.work_update", { ...task, source: "agent", source_ref: "task:search", kind: "finding", summary: "找到视觉产品经理线索" }),
    ]);

    expect(result?.timeline.map((item) => item.sourceKind)).toEqual([
      "brain_thinking",
      "platform_fact",
      "brain_message",
      "agent_thinking",
      "agent_work",
      "agent_message",
    ]);
    expect(result?.tasks[0]).toMatchObject({
      taskId: "task-1", agentId: "hr-bot", agentLabel: "HR Agent", status: "running",
    });
  });

  it("deduplicates replay, sorts out-of-order input, and marks interrupted thinking", () => {
    const interrupted = event(3, "agent.thinking_summary", {
      ...task, source: "provider", source_ref: "agent-run", summary: "未完成的摘要", status: "interrupted",
    });
    const result = projectWorkroom([
      interrupted,
      event(1, "agent.task_dispatched", task),
      { ...interrupted },
      event(2, "agent.task_accepted", { ...task, status: "running" }),
    ]);

    expect(result?.timeline.map((item) => item.seq)).toEqual([1, 2, 3]);
    expect(result?.timeline[result.timeline.length - 1]?.interrupted).toBe(true);
  });

  it("projects artifacts, unavailable Agents, partial failure, and completion", () => {
    const marketing = { ...task, task_id: "task-2", child_session_id: "child-2", agent_id: "marketing-gtm-bot" };
    const result = projectWorkroom([
      event(1, "agent.task_dispatched", task),
      event(2, "agent.task_dispatched", marketing),
      event(3, "agent.artifact", { ...task, source: "agent", source_ref: "artifact:1", summary: "人才地图", artifact_refs: ["attachment-1"] }),
      event(4, "agent.task_completed", { ...task, status: "completed", summary: "人才搜索完成" }),
      event(5, "agent.task_unavailable", { ...marketing, status: "unavailable", summary: "本地 Agent 离线" }),
      event(6, "brain.answer_submitted", { status: "completed" }),
    ]);

    expect(result?.status).toBe("partially_completed");
    expect(result?.deliverables).toEqual([
      expect.objectContaining({ taskId: "task-1", attachmentRef: "attachment-1", label: "人才地图" }),
    ]);
    expect(result?.tasks.map((item) => item.status)).toEqual(["completed", "unavailable"]);
    expect(result?.defaultExpanded).toBe(false);
  });

  it("ignores unknown events and turns malformed allowlisted events into a Platform fact", () => {
    const result = projectWorkroom([
      event(1, "agent.task_dispatched", task),
      event(2, "agent.work_update", { ...task, kind: "pretending" }),
      event(3, "unknown.mock_progress", { summary: "正在深入思考" }),
    ]);

    expect(result?.timeline).toHaveLength(2);
    expect(result?.timeline[1]).toMatchObject({
      sourceKind: "platform_fact",
      text: "平台未能显示这条专业 Agent 更新",
    });
    expect(JSON.stringify(result)).not.toContain("正在深入思考");
  });
});
