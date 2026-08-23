import type { ConversationMessage } from "../../conversationTypes";
import { MessageMarkdown } from "../MessageMarkdown";


function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}


export function ConversationMessages({
  messages,
  assistantLabel = "Agent 大脑",
}: {
  messages: ConversationMessage[];
  assistantLabel?: string;
}) {
  const ordered = [...new Map(messages.map((message) => [message.message_id, message])).values()]
    .sort((left, right) => left.seq - right.seq);
  return <section className="conversation-messages" aria-label="对话内容" aria-live="polite">
    {ordered.map((message) => <article
      className={`conversation-message conversation-${message.role}`}
      data-message-id={message.message_id}
      key={message.message_id}
    >
      <header>
        <strong>{message.role === "user" ? "你" : message.role === "assistant" ? assistantLabel : "系统"}</strong>
        <time dateTime={message.created_at}>{timeLabel(message.created_at)}</time>
      </header>
      {message.role === "user"
        ? <p className="conversation-user-copy">{message.content}</p>
        : <MessageMarkdown content={message.content} />}
    </article>)}
  </section>;
}
