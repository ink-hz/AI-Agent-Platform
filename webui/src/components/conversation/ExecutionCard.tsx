import type { ConversationEvent, ConversationMode } from "../../conversationTypes";
import { PlatformLink } from "../PlatformLink";
import { professionalAgentLabel } from "./agentLabels";
import { UserInputRequest } from "./UserInputRequest";


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
  "brain.started": "Agent 大脑开始处理",
  "brain.step_started": "Agent 大脑正在决策",
  "agent.task_dispatched": "已分派专业 Agent",
  "agent.task_accepted": "专业 Agent 已接收",
  "agent.task_progress": "专业 Agent 正在执行",
  "agent.task_completed": "专业 Agent 已完成",
  "agent.task_failed": "专业 Agent 执行失败",
  "agent.task_timed_out": "专业 Agent 执行超时",
  "agent.task_unavailable": "专业 Agent 暂不可用",
  "brain.batch_settled": "本批 Agent 任务已结束",
  "brain.resumed": "Agent 大脑已读取结果",
  "brain.user_input_requested": "等待你补充信息",
  "brain.answer_submitted": "Agent 大脑已提交回答",
  "brain.failed": "Agent 大脑本轮未完成",
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
  disabled = false,
  pending = false,
  onResumeUserInput,
}: {
  events: ConversationEvent[];
  mode: ConversationMode;
  directAgentId: string | null;
  disabled?: boolean;
  pending?: boolean;
  onResumeUserInput?: (answer: string) => void;
}) {
  const ordered = [...new Map(events.map((event) => [event.event_id, event])).values()]
    .sort((left, right) => left.seq - right.seq);
  const selectedAgent = ordered.map((event) => (
    professionalAgentLabel(event.payload.selected_agent_id) ?? professionalAgentLabel(event.payload.agent_id)
  )).find(Boolean) ?? professionalAgentLabel(directAgentId);
  const taskEvents = ordered.filter((item) => item.event_type.startsWith("agent.task_"));
  const taskAgents = [...new Set(taskEvents.map((item) => (
    typeof item.payload.agent_id === "string" ? item.payload.agent_id : "unknown"
  )))];
  const latestTerminalSeq = taskEvents
    .filter((item) => [
      "agent.task_completed", "agent.task_failed", "agent.task_timed_out", "agent.task_unavailable",
    ].includes(item.event_type))
    .reduce((maximum, item) => Math.max(maximum, item.seq), 0);
  const brainResumed = ordered.some((item) => (
    item.event_type === "brain.resumed" && item.seq > latestTerminalSeq
  ));
  const waitingForBrain = latestTerminalSeq > 0 && !brainResumed;
  const waitingUser = [...ordered].reverse().find((item) => (
    item.event_type === "brain.user_input_requested"
  ));
  const question = waitingUser?.payload.objective_summary;
  return <div className="execution-wrap"><details className="execution-card">
    <summary>
      <span><strong>执行过程</strong><small>{selectedAgent ?? (mode === "brain" ? "Agent 大脑" : "专业 Agent")}</small></span>
      <b>{taskAgents.length ? `${taskAgents.length} 个 Agent 任务` : `${ordered.length} 个记录`}</b>
    </summary>
    <ol>
      {ordered.length === 0
        ? <li className="execution-empty">正在等待第一条执行记录…</li>
        : ordered.map((event) => {
          const label = professionalAgentLabel(event.payload.agent_id);
          const completed = event.event_type === "agent.task_completed";
          return <li key={event.event_id}>
          <div><strong>{completed && label ? `${label} 已完成` : EVENT_LABELS[event.event_type] ?? "执行更新"}</strong><span>{eventSummary(event)}</span></div>
          {event.mission_id && <PlatformLink href={`/missions/${encodeURIComponent(event.mission_id)}`}>诊断详情</PlatformLink>}
        </li>;})}
      {waitingForBrain && <li className="execution-waiting-brain">等待 Agent 大脑继续处理</li>}
    </ol>
  </details>
  {waitingUser && typeof question === "string" && onResumeUserInput && <UserInputRequest
    disabled={disabled}
    onSubmit={onResumeUserInput}
    pending={pending}
    question={question}
  />}
  </div>;
}
