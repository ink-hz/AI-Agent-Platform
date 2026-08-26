import type { ConversationEvent, ConversationMode } from "../../conversationTypes";
import { UserInputRequest } from "./UserInputRequest";


function eventSummary(event: ConversationEvent): string | null {
  for (const key of ["summary", "public_reason", "objective_summary", "text"]) {
    const value = event.payload[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
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
  const visible = ordered.flatMap((event) => {
    const summary = eventSummary(event);
    return summary ? [{ event, summary }] : [];
  });
  const waitingUser = [...ordered].reverse().find((item) => (
    item.event_type === "brain.user_input_requested"
  ));
  const question = waitingUser?.payload.objective_summary;
  void mode; void directAgentId;
  return <div className="execution-wrap">{visible.length > 0 && <details className="execution-card">
    <summary>
      <span><strong>协作记录</strong><small>真实事件</small></span>
      <b>{visible.length} 条</b>
    </summary>
    <ol>
      {visible.map(({ event, summary }) => <li key={event.event_id}>
        <div><strong>{typeof event.payload.agent_name === "string" ? event.payload.agent_name : "Agent"}</strong><span>{summary}</span></div>
      </li>)}
    </ol>
  </details>}
  {waitingUser && typeof question === "string" && onResumeUserInput && <UserInputRequest
    disabled={disabled}
    onSubmit={onResumeUserInput}
    pending={pending}
    question={question}
  />}
  </div>;
}
