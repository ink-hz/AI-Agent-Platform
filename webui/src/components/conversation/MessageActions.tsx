import { useEffect, useState } from "react";
import { Check, CircleAlert, Copy, ThumbsDown, ThumbsUp } from "lucide-react";

import { copyVisibleText } from "../../clipboard";
import type { ConversationFeedbackReason, ConversationFeedbackRating } from "../../conversationTypes";

const REASONS: ReadonlyArray<readonly [ConversationFeedbackReason, string]> = [
  ["inaccurate", "信息不准确"], ["incomplete", "信息不完整"], ["unclear", "表达不清楚"],
  ["unresolved", "没有解决问题"], ["file_format", "文件或格式有问题"],
  ["source_timeliness", "来源或时效有问题"], ["other", "其他"],
];

export function MessageActions({ copyText, feedbackState, onCopy = copyVisibleText, onFeedback, onRetry }: {
  copyText: () => string;
  feedbackState?: ConversationFeedbackRating | "pending" | "error";
  onCopy?: (text: string) => Promise<boolean>;
  onFeedback?: (rating: ConversationFeedbackRating, reason: ConversationFeedbackReason | null, comment: string | null) => void;
  onRetry?: () => void;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const [improving, setImproving] = useState(false);
  const [reason, setReason] = useState<ConversationFeedbackReason | null>(null);
  const [comment, setComment] = useState("");
  useEffect(() => {
    if (copyState === "idle") return;
    const timer = window.setTimeout(() => setCopyState("idle"), 1800);
    return () => window.clearTimeout(timer);
  }, [copyState]);
  const disabled = Boolean(feedbackState && feedbackState !== "error");
  const changeComment = (value: string) => setComment(Array.from(value).slice(0, 1000).join(""));
  const copyLabel = copyState === "copied" ? "已复制" : copyState === "error" ? "复制失败" : "复制回答";
  return <footer className="conversation-message-actions">
    <div className="conversation-message-action-row">
      <button aria-label={copyLabel} className={`copy-answer-button ${copyState}`} onClick={() => void onCopy(copyText()).then((ok) => setCopyState(ok ? "copied" : "error")).catch(() => setCopyState("error"))} title={copyLabel} type="button">
        {copyState === "copied" ? <Check size={15} /> : copyState === "error" ? <CircleAlert size={15} /> : <Copy size={15} />}
      </button>
      {onFeedback && <><button aria-label="有用" className={`feedback-icon-button ${feedbackState === "helpful" ? "is-selected" : ""}`} disabled={disabled} onClick={() => onFeedback("helpful", null, null)} title="有用" type="button"><ThumbsUp size={15} /></button>
        <button aria-label="不达标" className={`feedback-icon-button ${feedbackState === "unhelpful" ? "is-selected" : ""}`} disabled={disabled} onClick={() => setImproving(true)} title="不达标" type="button"><ThumbsDown size={15} /></button></>}
      {onRetry && <button onClick={onRetry} type="button">重新生成</button>}
      {(feedbackState === "helpful" || feedbackState === "unhelpful") && <span>已记录你的反馈</span>}
    </div>
    {improving && !disabled && <div className="conversation-feedback-detail">
      <p>请选择最需要改进的一项</p>
      <div className="conversation-feedback-reasons">{REASONS.map(([value, label]) => <button aria-pressed={reason === value} className={reason === value ? "is-selected" : ""} key={value} onClick={() => setReason(value)} type="button">{label}</button>)}</div>
      <label>补充说明（可选）<textarea aria-label="补充改进建议" onChange={(event) => changeComment(event.target.value)} placeholder="具体哪里需要改进？" value={comment} /></label>
      <small>{Array.from(comment).length}/1000</small>
      <div><button disabled={!reason} onClick={() => { onFeedback?.("unhelpful", reason, comment.trim() || null); setImproving(false); }} type="button">提交反馈</button>
        <button onClick={() => setImproving(false)} type="button">取消</button></div>
    </div>}
    {feedbackState === "error" && <small role="alert">操作暂未保存，请重试。</small>}
  </footer>;
}
