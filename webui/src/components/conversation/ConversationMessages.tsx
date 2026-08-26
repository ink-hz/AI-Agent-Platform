import { Fragment, type ReactNode, useState } from "react";

import type {
  ConversationFeedbackReason,
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
  renderAfterUserTurn,
}: {
  messages: ConversationMessage[];
  assistantLabel?: string;
  feedback?: Record<string, ConversationFeedbackRating | "pending" | "error">;
  onFeedback?: (
    messageId: string,
    rating: ConversationFeedbackRating,
    reason: ConversationFeedbackReason | null,
    comment: string | null,
  ) => void;
  renderAfterUserTurn?: (turnId: string) => ReactNode;
}) {
  const [improving, setImproving] = useState<string | null>(null);
  const [reasons, setReasons] = useState<Record<string, ConversationFeedbackReason | null>>({});
  const [comments, setComments] = useState<Record<string, string>>({});
  const ordered = [...new Map(messages.map((message) => [message.message_id, message])).values()]
    .sort((left, right) => left.seq - right.seq);
  return <section className="conversation-messages" aria-label="对话内容" aria-live="polite">
    {ordered.map((message) => <Fragment key={message.message_id}><article
      className={`conversation-message conversation-${message.role}`}
      data-message-id={message.message_id}
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
          onClick={() => onFeedback(message.message_id, "helpful", null, null)}
          type="button"
        >有帮助</button>
        <button
          aria-label="这个回答需改进"
          className={feedback[message.message_id] === "unhelpful" ? "is-selected" : ""}
          disabled={Boolean(feedback[message.message_id] && feedback[message.message_id] !== "error")}
          onClick={() => setImproving(message.message_id)}
          type="button"
        >需改进</button>
        {improving === message.message_id && !feedback[message.message_id] && <div className="conversation-feedback-detail">
          <div>{([
            ["inaccurate", "信息不准确"], ["incomplete", "信息不完整"], ["unclear", "表达不清楚"],
            ["unresolved", "没有解决问题"], ["other", "其他"],
          ] as const).map(([value, label]) => <button className={reasons[message.message_id] === value ? "is-selected" : ""} key={value} onClick={() => setReasons((current) => ({ ...current, [message.message_id]: value }))} type="button">{label}</button>)}</div>
          <textarea aria-label="补充改进建议" maxLength={1000} onChange={(event) => setComments((current) => ({ ...current, [message.message_id]: event.target.value }))} placeholder="可选：补充哪里需要改进" value={comments[message.message_id] ?? ""} />
          <button disabled={!reasons[message.message_id]} onClick={() => onFeedback(message.message_id, "unhelpful", reasons[message.message_id] ?? null, comments[message.message_id]?.trim() || null)} type="button">提交反馈</button>
          <button onClick={() => setImproving(null)} type="button">取消</button>
        </div>}
        {feedback[message.message_id] === "error" && <small role="alert">反馈暂未保存，请重试。</small>}
      </footer>}
    </article>
    {message.role === "user" && message.turn_id && renderAfterUserTurn?.(message.turn_id)}
    </Fragment>)}
  </section>;
}
