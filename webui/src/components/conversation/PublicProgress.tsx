import type { ReactNode } from "react";

import type { ConversationEvent, ConversationMode } from "../../conversationTypes";


export interface PublicProgressProps {
  mode: ConversationMode;
  events: ConversationEvent[];
  active: boolean;
  assistantLabel: string;
  stopButton: ReactNode;
}

export function PublicProgress({
  mode,
  events,
  active,
  assistantLabel,
  stopButton,
}: PublicProgressProps) {
  void events;
  if (mode === "direct_agent") {
    return active ? <section className="conversation-running" aria-live="polite" role="status">
      <span>请求已交给 {assistantLabel}，等待真实结果返回。</span>
      {stopButton}
    </section> : null;
  }
  return active ? <section className="conversation-running" aria-live="polite" role="status">
    <span>本轮仍在执行，你可以继续补充要求。</span>
    {stopButton}
  </section> : null;
}
