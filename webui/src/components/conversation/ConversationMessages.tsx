import { Fragment, useRef, type ReactNode } from "react";

import type {
  ArtifactVersion,
  ConversationAttachment,
  ConversationCitation,
  ConversationFeedbackReason,
  ConversationFeedbackRating,
  ConversationMessage,
} from "../../conversationTypes";
import { MessageMarkdown } from "../MessageMarkdown";
import { ArtifactVersionList } from "./ArtifactVersionList";
import { CitationList } from "./CitationList";
import { MessageActions } from "./MessageActions";

function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function projectedVersions(message: ConversationMessage): ArtifactVersion[] {
  return message.output_attachments.map((attachment) => ({
    artifactKey: attachment.attachmentId,
    versionNo: 1,
    producerVersionId: attachment.attachmentId,
    current: attachment.state === "ready",
    status: attachment.state === "ready" ? "ready" : attachment.state === "rejected" || attachment.state === "quarantined"
      ? "failed" : "processing",
    attachment: attachment.state === "ready" ? attachment : null,
  }));
}

function SearchRecoveryNotice({ message, onRetry }: { message: ConversationMessage; onRetry?: () => void }) {
  const recovery = message.search_recovery;
  if (!recovery) return null;
  const time = timeLabel(recovery.lastAttemptAt);
  if (recovery.status === "unavailable") return <aside className="conversation-search-recovery" role="status">
    <strong>联网检索暂时不可用</strong><span>已尝试 {recovery.attemptCount} 次{time ? `，最后一次 ${time}` : ""}。本轮已保留，可以从原进度继续。</span>
    {recovery.resumable && onRetry && <button onClick={onRetry} type="button">继续重试</button>}
  </aside>;
  if (recovery.status === "no_results") return <aside className="conversation-search-recovery" role="status"><strong>检索完成，未找到结果</strong>{recovery.coverageNote && <span>{recovery.coverageNote}</span>}</aside>;
  return <aside className="conversation-search-recovery is-partial" role="status"><strong>已返回部分检索结果</strong>{recovery.coverageNote && <span>{recovery.coverageNote}</span>}</aside>;
}

function AssistantMessage({ message, assistantLabel, feedbackState, citations, versions, onFeedback, onOpenAttachment, onDownloadAll, onRetry }: {
  message: ConversationMessage;
  assistantLabel: string;
  feedbackState?: ConversationFeedbackRating | "pending" | "error";
  citations: ConversationCitation[];
  versions: ArtifactVersion[];
  onFeedback?: (messageId: string, rating: ConversationFeedbackRating, reason: ConversationFeedbackReason | null, comment: string | null) => void;
  onOpenAttachment?: (attachment: ConversationAttachment, purpose: "preview" | "download") => void;
  onDownloadAll?: () => void;
  onRetry?: () => void;
}) {
  const visible = useRef<HTMLDivElement>(null);
  return <article className="conversation-message conversation-assistant" data-message-id={message.message_id}>
    <header><strong>{assistantLabel}</strong><time dateTime={message.created_at}>{timeLabel(message.created_at)}</time></header>
    <div ref={visible}><MessageMarkdown content={message.content} /></div>
    <ArtifactVersionList onDownloadAll={onDownloadAll} onOpen={onOpenAttachment} versions={versions} />
    <CitationList citations={citations} />
    <SearchRecoveryNotice message={message} onRetry={onRetry} />
    {message.delivery_status === "completed" && <MessageActions
      copyText={() => visible.current?.innerText || visible.current?.textContent || message.content}
      feedbackState={feedbackState}
      onFeedback={onFeedback ? (rating, reason, comment) => onFeedback(message.message_id, rating, reason, comment) : undefined}
      onRetry={onRetry}
    />}
  </article>;
}

export function ConversationMessages({
  messages,
  assistantLabel = "Agent 大脑",
  feedback = {},
  citations = {},
  artifactVersions = {},
  onFeedback,
  onOpenAttachment,
  onDownloadAll,
  onRetry,
  renderAfterUserTurn,
}: {
  messages: ConversationMessage[];
  assistantLabel?: string;
  feedback?: Record<string, ConversationFeedbackRating | "pending" | "error">;
  citations?: Record<string, ConversationCitation[]>;
  artifactVersions?: Record<string, ArtifactVersion[]>;
  onFeedback?: (messageId: string, rating: ConversationFeedbackRating, reason: ConversationFeedbackReason | null, comment: string | null) => void;
  onOpenAttachment?: (attachment: ConversationAttachment, purpose: "preview" | "download") => void;
  onDownloadAll?: (messageId: string) => void;
  onRetry?: (message: ConversationMessage) => void;
  renderAfterUserTurn?: (turnId: string) => ReactNode;
}) {
  const ordered = [...new Map(messages.map((message) => [message.message_id, message])).values()]
    .sort((left, right) => left.seq - right.seq);
  return <section className="conversation-messages" aria-label="对话内容" aria-live="polite">
    {ordered.map((message) => <Fragment key={message.message_id}>
      {message.role === "assistant" ? <AssistantMessage
        assistantLabel={assistantLabel}
        citations={citations[message.message_id] ?? message.citations ?? []}
        feedbackState={feedback[message.message_id]}
        message={message}
        onDownloadAll={message.output_attachments.length > 1 && onDownloadAll ? () => onDownloadAll(message.message_id) : undefined}
        onFeedback={onFeedback}
        onOpenAttachment={onOpenAttachment}
        onRetry={onRetry && message.search_recovery?.resumable ? () => onRetry(message) : undefined}
        versions={artifactVersions[message.message_id] ?? message.artifact_versions ?? projectedVersions(message)}
      /> : <article className={`conversation-message conversation-${message.role}`} data-message-id={message.message_id}>
        <header><strong>{message.role === "user" ? "你" : "系统"}</strong><time dateTime={message.created_at}>{timeLabel(message.created_at)}</time></header>
        <p className="conversation-user-copy">{message.content}</p>
      </article>}
      {message.role === "user" && message.turn_id && renderAfterUserTurn?.(message.turn_id)}
    </Fragment>)}
  </section>;
}
