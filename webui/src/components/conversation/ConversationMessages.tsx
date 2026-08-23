import type {
  ConversationFeedbackRating,
  ConversationMessage,
} from "../../conversationTypes";
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
  feedback = {},
  onFeedback,
}: {
  messages: ConversationMessage[];
  assistantLabel?: string;
  feedback?: Record<string, ConversationFeedbackRating | "pending" | "error">;
  onFeedback?: (messageId: string, rating: ConversationFeedbackRating) => void;
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
      {message.role === "assistant" && message.delivery_status === "completed" && onFeedback && <footer className="conversation-feedback">
        <span>{feedback[message.message_id] === "helpful" || feedback[message.message_id] === "unhelpful" ? "已记录你的反馈" : "这个回答怎么样？"}</span>
        <button
          aria-label="这个回答有帮助"
          className={feedback[message.message_id] === "helpful" ? "is-selected" : ""}
          disabled={Boolean(feedback[message.message_id] && feedback[message.message_id] !== "error")}
          onClick={() => onFeedback(message.message_id, "helpful")}
          type="button"
        >有帮助</button>
        <button
          aria-label="这个回答没有帮助"
          className={feedback[message.message_id] === "unhelpful" ? "is-selected" : ""}
          disabled={Boolean(feedback[message.message_id] && feedback[message.message_id] !== "error")}
          onClick={() => onFeedback(message.message_id, "unhelpful")}
          type="button"
        >没帮助</button>
        {feedback[message.message_id] === "error" && <small role="alert">反馈暂未保存，请重试。</small>}
      </footer>}
    </article>)}
  </section>;
}
