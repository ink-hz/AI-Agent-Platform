import type { ConversationEvent, ConversationMode } from "../../conversationTypes";
import { PlatformLink } from "../PlatformLink";
import { professionalAgentLabel } from "./agentLabels";


const EVENT_LABELS: Record<string, string> = {
  "conversation.started": "对话已建立",
  "message.accepted": "消息已接收",
  "turn.accepted": "本轮任务已建立",
  "turn.running": "正在执行",
  "task.dispatched": "已交付专业 Agent",
  "agent.progress": "专业 Agent 正在处理",
  "message.completed": "回答已保存",
  "message.failed": "本轮未能完成",
  "turn.completed": "本轮已完成",
  "turn.failed": "本轮未完成",
  "turn.cancelled": "本轮已停止",
  "turn.interrupted": "本轮已中断",
};


function eventSummary(event: ConversationEvent): string {
  for (const key of ["text", "summary", "objective", "status"]) {
    const value = event.payload[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return EVENT_LABELS[event.event_type] ?? "执行状态已更新";
}


export function ExecutionCard({
  events,
  mode,
  directAgentId,
}: {
  events: ConversationEvent[];
  mode: ConversationMode;
  directAgentId: string | null;
}) {
  const ordered = [...new Map(events.map((event) => [event.event_id, event])).values()]
    .sort((left, right) => left.seq - right.seq);
  const selectedAgent = ordered.map((event) => (
    professionalAgentLabel(event.payload.selected_agent_id) ?? professionalAgentLabel(event.payload.agent_id)
  )).find(Boolean) ?? professionalAgentLabel(directAgentId);
  return <details className="execution-card">
    <summary>
      <span><strong>执行过程</strong><small>{selectedAgent ?? (mode === "brain" ? "Agent 大脑" : "专业 Agent")}</small></span>
      <b>{ordered.length} 个记录</b>
    </summary>
    <ol>
      {ordered.length === 0
        ? <li className="execution-empty">正在等待第一条执行记录…</li>
        : ordered.map((event) => <li key={event.event_id}>
          <div><strong>{EVENT_LABELS[event.event_type] ?? "执行更新"}</strong><span>{eventSummary(event)}</span></div>
          {event.mission_id && <PlatformLink href={`/missions/${encodeURIComponent(event.mission_id)}`}>诊断详情</PlatformLink>}
        </li>)}
    </ol>
  </details>;
}
