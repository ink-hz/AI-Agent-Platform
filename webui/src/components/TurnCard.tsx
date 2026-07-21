import { useState } from "react";

import { fetchTrace } from "../api";
import type { TraceDetail, TurnDetail } from "../types";
import { TraceTimeline } from "./TraceTimeline";


function duration(value: number | null) {
  if (value === null) return null;
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}


export function TurnCard({ turn }: { turn: TurnDetail }) {
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
  return <article className="turn-card">
    <header className="turn-head"><span>TURN {String(turn.turn_index).padStart(2, "0")}</span><div>{turn.outcome && <b>{turn.outcome}</b>}{turn.fallback_used && <b className="turn-fallback">fallback</b>}{duration(turn.duration_ms) && <time>{duration(turn.duration_ms)}</time>}</div></header>
    <section className="message-block question-block"><span>Question</span><p>{turn.question || "No question captured"}</p></section>
    <section className="message-block answer-block"><span>Answer</span><p>{turn.answer || "No answer captured"}</p></section>
    {turn.evidence.length > 0 && <section className="turn-evidence"><h3>Evidence</h3><div>{turn.evidence.map((item, index) => <article key={`${item.title}-${index}`}><span>{item.kind}</span><strong>{item.title}</strong>{item.reference && <p>{item.reference}</p>}</article>)}</div></section>}
    {turn.evidence.length === 0 && turn.evidence_availability !== "available" && <p className="availability-note">Evidence detail: {turn.evidence_availability}</p>}
    {(turn.feedback.length > 0 || turn.reviews.length > 0 || turn.improvements.length > 0) && <section className="turn-signals">
      {turn.feedback.map((item) => <div className={`signal signal-${item.sentiment}`} key={item.feedback_key}><span>Feedback · {item.sentiment}</span><p>{item.comment || item.reason_code || item.raw_rating}</p></div>)}
      {turn.reviews.map((item) => <div className="signal signal-review" key={item.review_key}><span>Review · {item.normalized_priority}</span><p>{item.notes || item.corrected_answer || item.status}</p></div>)}
      {turn.improvements.map((item) => <div className="signal signal-improvement" key={item.item_key}><span>{item.item_type} · {item.status}</span><p>{item.title || item.summary}</p></div>)}
    </section>}
    {turn.trace_key && <div className="trace-action"><button aria-expanded={open} onClick={toggleTrace}>{open ? "Hide Trace" : "View Trace"}</button><span>{turn.trace_key}</span></div>}
    {open && (trace ? <TraceTimeline trace={trace} /> : <div className="trace-loading">{traceState === "loading" ? "Loading Trace…" : "Trace detail is not available for this turn."}</div>)}
  </article>;
}
