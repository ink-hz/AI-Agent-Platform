import type { TraceDetail } from "../types";


function duration(value: number | null) {
  if (value === null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value}ms`;
}


function compact(value: Record<string, unknown>) {
  if (Object.keys(value).length === 0) return null;
  return JSON.stringify(value);
}


export function TraceTimeline({ trace }: { trace: TraceDetail }) {
  return <section className="trace-panel">
    <div className="trace-summary">
      <div><span>Trace</span><strong>{trace.status}</strong></div>
      <div><span>Duration</span><strong>{duration(trace.duration_ms)}</strong></div>
      <div><span>Model</span><strong>{trace.model || trace.engine || "Not reported"}</strong></div>
      <div><span>Tokens</span><strong>{trace.input_tokens === null ? "—" : `${trace.input_tokens.toLocaleString("en-US")} input`}{trace.output_tokens !== null && ` · ${trace.output_tokens.toLocaleString("en-US")} output`}</strong></div>
    </div>
    {trace.detail_availability === "unavailable" && <div className="trace-unavailable"><strong>Engineering Trace is not captured by this source.</strong><p>Available high-level stages are shown below; spans, model calls, and tool internals are unavailable.</p></div>}
    {trace.detail_availability === "restricted" && <div className="trace-unavailable"><strong>Trace detail is restricted at the source.</strong></div>}
    {trace.steps.length === 0 ? <div className="trace-unavailable"><strong>No execution steps were recorded for this turn.</strong></div>
      : <ol className="trace-timeline">{trace.steps.map((step, index) => <li key={step.step_key}>
          <span className={`trace-node trace-${step.kind}`}>{index + 1}</span>
          <div className="trace-step-head"><div><b>{step.name}</b><span>{step.kind}</span></div><time>{duration(step.duration_ms)}</time></div>
          {step.status && <span className={`trace-status trace-status-${step.status}`}>{step.status}</span>}
          {compact(step.input_summary) && <p><strong>Input</strong> {compact(step.input_summary)}</p>}
          {compact(step.output_summary) && <p><strong>Output</strong> {compact(step.output_summary)}</p>}
          {step.error_summary && <p className="trace-error">{step.error_summary}</p>}
        </li>)}</ol>}
  </section>;
}
