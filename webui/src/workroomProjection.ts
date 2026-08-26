import type { ConversationEvent } from "./conversationTypes";
import { professionalAgentLabel } from "./components/conversation/agentLabels";
import type {
  WorkroomDeliverable,
  WorkroomSourceKind,
  WorkroomStatus,
  WorkroomTask,
  WorkroomTaskStatus,
  WorkroomTimelineItem,
  WorkroomTurn,
} from "./workroomTypes";


const TERMINAL_TASK_EVENTS: Record<string, WorkroomTaskStatus> = {
  "agent.task_completed": "completed",
  "agent.task_failed": "failed",
  "agent.task_timed_out": "timed_out",
  "agent.task_unavailable": "unavailable",
  "agent.cancelled": "cancelled",
};
const PLATFORM_FACT_EVENTS = new Set([
  "agent.task_dispatched",
  "agent.task_accepted",
  "agent.task_completed",
  "agent.task_failed",
  "agent.task_timed_out",
  "agent.task_unavailable",
  "agent.cancelled",
  "agent.task_recovered",
  "brain.waiting_agents",
  "brain.user_intervention",
  "brain.agent_stop_requested",
  "brain.answer_submitted",
  "brain.failed",
]);
const AGENT_EVENT_TYPES = new Set([
  "agent.thinking_summary",
  "agent.work_update",
  "agent.message",
  "agent.artifact",
  "agent.question",
]);
const WORK_KINDS = new Set([
  "plan", "progress", "finding", "question", "blocker", "decision", "artifact", "result",
]);


function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}


function stringItems(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    : [];
}


function orderedUnique(events: ConversationEvent[]): ConversationEvent[] {
  const selected = new Map<string, ConversationEvent>();
  [...events]
    .sort((left, right) => left.seq - right.seq || left.event_id.localeCompare(right.event_id))
    .forEach((event) => {
      if (!selected.has(event.event_id)) selected.set(event.event_id, event);
    });
  return [...selected.values()];
}


function taskStatus(value: unknown): WorkroomTaskStatus {
  return [
    "queued", "running", "waiting", "completed", "failed", "timed_out", "unavailable", "cancelled",
  ].includes(String(value)) ? value as WorkroomTaskStatus : "queued";
}


function platformText(event: ConversationEvent, label: string): string {
  const supplied = stringValue(event.payload.summary)
    ?? stringValue(event.payload.objective_summary)
    ?? stringValue(event.payload.public_reason);
  if (supplied) return supplied;
  return ({
    "agent.task_dispatched": `${label} 已收到任务`,
    "agent.task_accepted": `${label} 已开始工作`,
    "agent.task_completed": `${label} 已完成任务`,
    "agent.task_failed": `${label} 未能完成任务`,
    "agent.task_timed_out": `${label} 执行超时`,
    "agent.task_unavailable": `${label} 当前不可用`,
    "agent.cancelled": `${label} 任务已停止`,
    "agent.task_recovered": `${label} 任务已恢复`,
    "brain.waiting_agents": "Agent 大脑正在等待专业 Agent 的真实更新",
    "brain.user_intervention": "已收到你对当前任务的补充",
    "brain.agent_stop_requested": "Agent 大脑已发送停止请求",
    "brain.answer_submitted": "Agent 大脑已完成本轮交付",
    "brain.failed": "Agent 大脑未能完成本轮交付",
  } as Record<string, string>)[event.event_type] ?? "平台已更新协作状态";
}


function malformedFact(event: ConversationEvent, taskId: string | null): WorkroomTimelineItem {
  return {
    eventId: event.event_id,
    taskId,
    seq: event.seq,
    sourceKind: "platform_fact",
    sourceLabel: "Platform",
    text: "平台未能显示这条专业 Agent 更新",
    createdAt: event.created_at,
    interrupted: false,
  };
}


function timelineItem(
  event: ConversationEvent,
  tasks: Map<string, WorkroomTask>,
): WorkroomTimelineItem | null {
  const taskId = stringValue(event.payload.task_id);
  const task = taskId ? tasks.get(taskId) : undefined;
  const agentLabel = task?.agentLabel ?? "专业 Agent";
  let sourceKind: WorkroomSourceKind;
  let sourceLabel: string;
  let text: string | null;

  if (event.event_type === "brain.thinking_summary") {
    if (
      event.payload.source !== "provider"
      || !stringValue(event.payload.source_ref)
      || !(text = stringValue(event.payload.summary))
    ) return null;
    sourceKind = "brain_thinking";
    sourceLabel = "Agent 大脑 · 思考摘要";
  } else if (event.event_type === "brain.agent_message_sent") {
    text = stringValue(event.payload.summary)
      ?? stringValue(event.payload.public_reason)
      ?? stringValue(event.payload.objective_summary);
    if (!task || !text) return malformedFact(event, taskId);
    sourceKind = "brain_message";
    sourceLabel = `Agent 大脑 → ${agentLabel}`;
  } else if (event.event_type === "agent.thinking_summary") {
    text = stringValue(event.payload.summary);
    if (
      !task || event.payload.source !== "provider"
      || !stringValue(event.payload.source_ref) || !text
    ) return malformedFact(event, taskId);
    sourceKind = "agent_thinking";
    sourceLabel = `${agentLabel} · 思考摘要`;
  } else if (event.event_type === "agent.work_update") {
    text = stringValue(event.payload.summary);
    if (!task || !WORK_KINDS.has(String(event.payload.kind)) || !text) {
      return malformedFact(event, taskId);
    }
    sourceKind = "agent_work";
    sourceLabel = `${agentLabel} · 工作进展`;
  } else if (event.event_type === "agent.message" || event.event_type === "agent.question") {
    text = stringValue(event.payload.summary);
    if (!task || !stringValue(event.payload.source_ref) || !text) {
      return malformedFact(event, taskId);
    }
    sourceKind = "agent_message";
    sourceLabel = event.event_type === "agent.question" ? `${agentLabel} · 问题` : agentLabel;
  } else if (event.event_type === "agent.artifact") {
    text = stringValue(event.payload.summary);
    if (!task || !text) return malformedFact(event, taskId);
    sourceKind = "agent_work";
    sourceLabel = `${agentLabel} · 成果`;
  } else if (PLATFORM_FACT_EVENTS.has(event.event_type)) {
    if (event.event_type.startsWith("agent.") && !task) return malformedFact(event, taskId);
    text = platformText(event, agentLabel);
    sourceKind = "platform_fact";
    sourceLabel = "Platform";
  } else if (AGENT_EVENT_TYPES.has(event.event_type)) {
    return malformedFact(event, taskId);
  } else {
    return null;
  }
  return {
    eventId: event.event_id,
    taskId: taskId ?? null,
    seq: event.seq,
    sourceKind,
    sourceLabel,
    text,
    createdAt: stringValue(event.payload.created_at) ?? event.created_at,
    interrupted: event.payload.status === "interrupted",
  };
}


function workroomStatus(events: ConversationEvent[], tasks: WorkroomTask[]): WorkroomStatus {
  if (events.some((event) => event.event_type === "brain.failed")) return "failed";
  const terminal = tasks.filter((task) => [
    "completed", "failed", "timed_out", "unavailable", "cancelled",
  ].includes(task.status));
  const submitted = events.some((event) => event.event_type === "brain.answer_submitted");
  if (!submitted && terminal.length !== tasks.length) return "running";
  const completed = tasks.filter((task) => task.status === "completed").length;
  if (completed === tasks.length) return "completed";
  if (completed > 0) return "partially_completed";
  if (tasks.every((task) => task.status === "cancelled")) return "cancelled";
  return "failed";
}


export function projectWorkroom(events: ConversationEvent[]): WorkroomTurn | null {
  const ordered = orderedUnique(events);
  const dispatched = ordered.filter((event) => event.event_type === "agent.task_dispatched");
  if (dispatched.length === 0) return null;
  const tasks = new Map<string, WorkroomTask>();
  for (const event of dispatched) {
    const taskId = stringValue(event.payload.task_id);
    const childSessionId = stringValue(event.payload.child_session_id);
    const agentId = stringValue(event.payload.agent_id);
    if (!taskId || !childSessionId || !agentId || tasks.has(taskId)) continue;
    tasks.set(taskId, {
      taskId,
      childSessionId,
      agentId,
      agentLabel: professionalAgentLabel(agentId) ?? "专业 Agent",
      objective: stringValue(event.payload.objective_summary) ?? "专业任务",
      publicReason: stringValue(event.payload.public_reason) ?? "需要专业 Agent 协作",
      status: taskStatus(event.payload.status),
      lastUpdate: null,
      artifactCount: 0,
    });
  }
  if (tasks.size === 0) return null;
  const deliverables: WorkroomDeliverable[] = [];
  for (const event of ordered) {
    const taskId = stringValue(event.payload.task_id);
    const task = taskId ? tasks.get(taskId) : undefined;
    if (!task) continue;
    const terminalStatus = TERMINAL_TASK_EVENTS[event.event_type];
    if (terminalStatus) task.status = terminalStatus;
    else if (event.event_type === "agent.task_accepted") task.status = "running";
    const update = stringValue(event.payload.summary);
    if (update) task.lastUpdate = update;
    if (event.event_type === "agent.artifact") {
      const refs = stringItems(event.payload.artifact_refs);
      task.artifactCount += refs.length;
      refs.forEach((attachmentRef) => deliverables.push({
        eventId: event.event_id,
        taskId: task.taskId,
        attachmentRef,
        label: update ?? "专业 Agent 成果",
      }));
    }
  }
  const selectedTasks = [...tasks.values()];
  const status = workroomStatus(ordered, selectedTasks);
  return {
    turnId: ordered.find((event) => event.turn_id)?.turn_id ?? "unknown-turn",
    status,
    defaultExpanded: status === "running",
    tasks: selectedTasks,
    timeline: ordered.flatMap((event) => {
      const selected = timelineItem(event, tasks);
      return selected ? [selected] : [];
    }),
    deliverables,
  };
}
