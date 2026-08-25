import type { ReactNode } from "react";

import type { ConversationEvent, ConversationMode } from "../../conversationTypes";


export interface PublicProgressProps {
  mode: ConversationMode;
  events: ConversationEvent[];
  active: boolean;
  assistantLabel: string;
  stopButton: ReactNode;
}

interface PublicBrainStep {
  key: string;
  label: string;
}

function publicBrainSteps(events: ConversationEvent[]): PublicBrainStep[] {
  return [...new Map(events.map((event) => [event.event_id, event])).values()]
    .sort((left, right) => left.seq - right.seq)
    .flatMap((event) => {
      const agent = typeof event.payload.agent_name === "string"
        ? event.payload.agent_name
        : "专业 Agent";
      const label: string | undefined = ({
        "brain.started": "Agent 大脑正在分析需求",
        "agent.task_dispatched": `已交给 ${agent}`,
        "agent.task_accepted": `${agent} 已接收任务`,
        "agent.task_progress": `${agent} 正在处理`,
        "agent.task_completed": `${agent} 已完成`,
        "agent.task_failed": `${agent} 未能完成`,
        "agent.task_timed_out": `${agent} 执行超时`,
        "agent.task_unavailable": `${agent} 暂不可用`,
        "brain.resumed": "Agent 大脑正在整合结果",
        "brain.answer_submitted": "Agent 大脑已提交回答",
        "brain.failed": "Agent 大脑本轮未能完成",
      } as Record<string, string>)[event.event_type];
      return label ? [{ key: event.event_id, label }] : [];
    });
}


export function PublicProgress({
  mode,
  events,
  active,
  assistantLabel,
  stopButton,
}: PublicProgressProps) {
  if (mode === "direct_agent") {
    return active ? <section className="conversation-running" aria-live="polite" role="status">
      <span>{assistantLabel} 正在处理…</span>
      {stopButton}
    </section> : null;
  }

  const steps = publicBrainSteps(events);
  return <div className="public-progress">
    {active && <section className="conversation-running" aria-live="polite" role="status">
      <span>Agent 大脑正在处理…</span>
      {stopButton}
    </section>}
    {steps.length > 0 && <details className="public-collaboration">
      <summary>查看协作过程</summary>
      <ol>{steps.map((step) => <li key={step.key}>{step.label}</li>)}</ol>
    </details>}
  </div>;
}
