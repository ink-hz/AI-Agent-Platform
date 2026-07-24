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
      <div><span>耗时</span><strong>{duration(trace.duration_ms)}</strong></div>
      <div><span>Model</span><strong>{trace.model || trace.engine || "未报告"}</strong></div>
      <div><span>Token</span><strong>{trace.input_tokens === null ? "—" : `${trace.input_tokens.toLocaleString("zh-CN")} 输入`}{trace.output_tokens !== null && ` · ${trace.output_tokens.toLocaleString("zh-CN")} 输出`}</strong></div>
    </div>
    {trace.detail_availability === "unavailable" && <div className="trace-unavailable"><strong>当前数据源未采集工程 Trace。</strong><p>下方仅展示已采集的高层阶段，不包含 span、模型调用和工具内部细节。</p></div>}
    {trace.detail_availability === "restricted" && <div className="trace-unavailable"><strong>数据源限制了 Trace 详情。</strong></div>}
    {trace.steps.length === 0 ? <div className="trace-unavailable"><strong>该轮没有记录执行步骤。</strong></div>
      : <ol className="trace-timeline">{trace.steps.map((step, index) => <li key={step.step_key}>
          <span className={`trace-node trace-${step.kind}`}>{index + 1}</span>
          <div className="trace-step-head"><div><b>{step.name}</b><span>{step.kind}</span></div><time>{duration(step.duration_ms)}</time></div>
          {step.status && <span className={`trace-status trace-status-${step.status}`}>{step.status}</span>}
          {compact(step.input_summary) && <p><strong>输入</strong> {compact(step.input_summary)}</p>}
          {compact(step.output_summary) && <p><strong>输出</strong> {compact(step.output_summary)}</p>}
          {step.error_summary && <p className="trace-error">{step.error_summary}</p>}
        </li>)}</ol>}
  </section>;
}
