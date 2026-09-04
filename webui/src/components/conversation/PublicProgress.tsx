import type { ReactNode } from "react";

import type { ConversationEvent, ConversationMode } from "../../conversationTypes";


export interface PublicProgressProps {
  mode: ConversationMode;
  events: ConversationEvent[];
  active: boolean;
  assistantLabel: string;
  stopButton: ReactNode;
}

const DIRECT_PROGRESS_EVENTS = new Set([
  "agent.task_dispatched",
  "agent.task_accepted",
  "agent.task_progress",
  "agent.thinking_summary",
  "agent.work_update",
  "agent.message",
]);

function progressText(event: ConversationEvent, assistantLabel: string): string | null {
  for (const key of ["summary", "text", "objective_summary", "public_reason"] as const) {
    const value = event.payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  if (event.event_type === "agent.task_dispatched") return "已进入队列";
  if (event.event_type === "agent.task_accepted") return `${assistantLabel} 已开始执行`;
  return null;
}

function directUpdates(events: ConversationEvent[], assistantLabel: string): string[] {
  const seen = new Set<string>();
  const updates: string[] = [];
  for (const event of [...events].sort((left, right) => left.seq - right.seq)) {
    if (!DIRECT_PROGRESS_EVENTS.has(event.event_type)) continue;
    const text = progressText(event, assistantLabel);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    updates.push(text);
  }
  return updates;
}

export function PublicProgress({
  mode,
  events,
  active,
  assistantLabel,
  stopButton,
}: PublicProgressProps) {
  if (mode === "direct_agent") {
    if (!active) return null;
    const updates = directUpdates(events, assistantLabel);
    return <section className="conversation-running conversation-running-direct" aria-live="polite" role="status">
      <div>
        <strong>{assistantLabel} 正在处理</strong>
        {updates.length > 0
          ? <ol>{updates.map((update) => <li key={update}>{update}</li>)}</ol>
          : <p>已进入队列</p>}
      </div>
      {stopButton}
    </section>;
  }
  return active ? <section className="conversation-running" aria-live="polite" role="status">
    <span>本轮仍在执行，你可以继续补充要求。</span>
    {stopButton}
  </section> : null;
}
