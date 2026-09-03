import { useState } from "react";

import { fetchTrace } from "../api";
import { formatMessageTime } from "../messageTime";
import { formatSenderIdentity } from "../senderIdentity";
import { turnAnswerPresentation } from "../turnAnswerPresentation";
import type { TraceDetail, TurnClosureSummary, TurnDetail } from "../types";
import { AttachmentList } from "./AttachmentList";
import { MessageMarkdown } from "./MessageMarkdown";
import { PlatformLink } from "./PlatformLink";
import { TraceTimeline } from "./TraceTimeline";


function duration(value: number | null) {
  if (value === null) return null;
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}


function availabilityLabel(value: TurnDetail["evidence_availability"]): string {
  if (value === "missing") return "未采集";
  if (value === "unavailable") return "暂不可用";
  if (value === "restricted") return "受限";
  return "可用";
}


const CLOSURE_STATUS_LABELS: Record<string, string> = {
  unknown: "治理状态暂不可用",
  pending_triage: "待归因",
  fixing: "修复中",
  awaiting_merge: "等待合并",
  awaiting_deploy: "等待部署",
  awaiting_replay: "等待复跑",
  awaiting_review: "等待语义复审",
  closed: "已闭环",
  duplicate: "重复事项",
  not_actionable: "无需处理",
  wont_fix: "暂不修复",
};

const MISSING_GATE_LABELS: Record<string, string> = {
  issue: "纳管事项",
  triage: "根因归类",
  replay: "真实复跑",
  semantic_review: "独立语义复审",
  merge: "合并证据",
  deployment: "部署证据",
};


export function TurnCard({ turn, closureSummary, governanceHref }: {
  turn: TurnDetail;
  closureSummary?: TurnClosureSummary;
  governanceHref?: string;
}) {
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [traceState, setTraceState] = useState<"idle" | "loading" | "missing">("idle");
  const toggleTrace = () => {
    const next = !open;
    setOpen(next);
    if (next && traceState === "idle" && !trace) {
      setTraceState("loading");
      fetchTrace(turn.turn_key).then((value) => { setTrace(value); setTraceState("idle"); }).catch(() => setTraceState("missing"));
    }
  };
  const questionTime = formatMessageTime(turn.question_at, turn.question_time_status);
  const answerTime = formatMessageTime(turn.answer_at, turn.answer_time_status);
  const hasNegativeFeedback = turn.feedback.some((item) => item.sentiment === "negative")
    || (turn.feedback_summary?.negative ?? 0) > 0;
  const feedbackSummaryLabels: Record<string, string> = {
    negative: "负向反馈", positive: "正向反馈", other: "其他反馈",
  };
  const governedClosureSummary = closureSummary && closureSummary.issue_id !== null ? closureSummary : undefined;
  const answerPresentation = turnAnswerPresentation(turn);
  return <article className="turn-card">
    <header className="turn-head"><span>第 {String(turn.turn_index).padStart(2, "0")} 轮</span><div>{turn.outcome && <b>{turn.outcome}</b>}{turn.fallback_used && <b className="turn-fallback">fallback</b>}{duration(turn.duration_ms) && <time>{duration(turn.duration_ms)}</time>}</div></header>
    <section className="message-block question-block"><div className="message-label"><span>用户提问</span><time className="message-time" {...(questionTime.dateTime ? { dateTime: questionTime.dateTime } : {})}>{questionTime.label}</time></div><div>{turn.source_kind === "metabot" && <small className="question-sender">{formatSenderIdentity(turn.sender_name, turn.sender_department)}</small>}{turn.question ? <MessageMarkdown content={turn.question} /> : <p>未记录用户提问</p>}</div></section>
    <AttachmentList attachments={turn.input_attachments} label="用户输入附件" />
    <section className="message-block answer-block"><div className="message-label"><span>Agent 回答</span><time className="message-time" {...(answerTime.dateTime ? { dateTime: answerTime.dateTime } : {})}>{answerTime.label}</time></div>{answerPresentation.kind === "answer"
      ? <MessageMarkdown content={answerPresentation.content} />
      : answerPresentation.kind === "failed"
        ? <p className="answer-failed"><strong>{answerPresentation.label}</strong>{answerPresentation.classification && <> · {answerPresentation.classification}</>}</p>
        : <p>{answerPresentation.label}</p>}</section>
    <AttachmentList attachments={turn.output_attachments} label="Agent 输出附件" />
    {turn.evidence.length > 0 && <section className="turn-evidence"><h3>证据</h3><div>{turn.evidence.map((item, index) => <article key={`${item.title}-${index}`}><span>{item.kind}</span><strong>{item.title}</strong>{item.reference && <p>{item.reference}</p>}</article>)}</div></section>}
    {turn.evidence.length === 0 && turn.evidence_availability !== "available" && <p className="availability-note">证据详情：{availabilityLabel(turn.evidence_availability)}</p>}
    {turn.feedback_availability && turn.feedback_availability !== "available" && <p className="availability-note">反馈详情：{availabilityLabel(turn.feedback_availability)}</p>}
    {turn.review_availability && turn.review_availability !== "available" && <p className="availability-note">复审详情：{availabilityLabel(turn.review_availability)}</p>}
    {(Object.keys(turn.feedback_summary ?? {}).length > 0 || Object.keys(turn.review_status_summary ?? {}).length > 0) && <section className="turn-signals" aria-label="投影信号摘要">
      {Object.entries(turn.feedback_summary ?? {}).map(([sentiment, count]) => <div className={`signal signal-${sentiment}`} key={`feedback-summary-${sentiment}`}><span>{feedbackSummaryLabels[sentiment] ?? `反馈 · ${sentiment}`} × {count}</span></div>)}
      {Object.entries(turn.review_status_summary ?? {}).map(([status, count]) => <div className="signal signal-review" key={`review-summary-${status}`}><span>复审状态 · {status} × {count}</span></div>)}
    </section>}
    {(turn.feedback.length > 0 || turn.reviews.length > 0 || turn.improvements.length > 0) && <section className="turn-signals">
      {turn.feedback.map((item) => <div className={`signal signal-${item.sentiment}`} key={item.feedback_key}><span>反馈 · {item.sentiment}</span><p>{item.comment || item.reason_code || item.raw_rating}</p></div>)}
      {turn.reviews.map((item) => <div className="signal signal-review" key={item.review_key}><span>复审 · {item.normalized_priority}</span><p>{item.notes || item.corrected_answer || item.status}</p></div>)}
      {turn.improvements.map((item) => <div className="signal signal-improvement" key={item.item_key}><span>{item.item_type} · {item.status}</span><p>{item.title || item.summary}</p></div>)}
    </section>}
    {governanceHref
      ? <div className="review-entry"><div><strong>{governedClosureSummary ? CLOSURE_STATUS_LABELS[governedClosureSummary.status] || governedClosureSummary.status : "尚未纳管"}</strong>{governedClosureSummary?.missing_gates?.[0] && <span>缺少：{MISSING_GATE_LABELS[governedClosureSummary.missing_gates[0]] || governedClosureSummary.missing_gates[0]}</span>}</div><PlatformLink href={governanceHref}>创建或查看问题</PlatformLink></div>
      : hasNegativeFeedback && <div className="review-entry"><div><strong>{CLOSURE_STATUS_LABELS[closureSummary?.status || "pending_triage"]}</strong>{closureSummary?.missing_gates?.[0] && <span>缺少：{MISSING_GATE_LABELS[closureSummary.missing_gates[0]] || closureSummary.missing_gates[0]}</span>}</div><PlatformLink href={`/admin/review?agent_id=${encodeURIComponent(turn.agent_id)}&turn_key=${encodeURIComponent(turn.turn_key)}`}>查看修复闭环</PlatformLink></div>}
    {turn.trace_key && <div className="trace-action"><button aria-expanded={open} onClick={toggleTrace}>{open ? "收起 Trace" : "查看 Trace"}</button><span>{turn.trace_key}</span></div>}
    {open && (trace ? <TraceTimeline trace={trace} /> : <div className="trace-loading">{traceState === "loading" ? "正在加载 Trace…" : "该轮暂无 Trace 详情。"}</div>)}
  </article>;
}
