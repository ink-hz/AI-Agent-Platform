import type { CSSProperties } from "react";

import { formatPublishedMetric } from "../../faeReportPresentation";
import type { MetricPresentation } from "../../faeReportPresentation";
import type { FaeReportMetric } from "../../faeReportTypes";
import { ReportMethodology } from "./ReportMethodology";


const NUMBER = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });


function RatioVisual({ metric }: { metric: FaeReportMetric }) {
  return <div className="fae-report-metric__ratio">
    <strong>{formatPublishedMetric(metric)}</strong>
    <span>{NUMBER.format(metric.numerator!)} / {NUMBER.format(metric.denominator!)}</span>
  </div>;
}


function DistributionVisual({ metric }: { metric: FaeReportMetric }) {
  const entries = Object.entries(metric.value as Record<string, number | string>);
  const numeric = entries
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
  const suppressed = entries.filter((entry): entry is [string, string] => typeof entry[1] === "string");
  const maximum = numeric[0]?.[1] ?? 0;
  return <ol className="fae-report-distribution">
    {numeric.map(([label, value]) => <li key={label}>
      <div><span>{label}</span><strong>{NUMBER.format(value)}</strong></div>
      <i aria-hidden="true" style={{ "--metric-share": maximum ? `${(value / maximum) * 100}%` : "0%" } as CSSProperties} />
    </li>)}
    {suppressed.map(([label, value]) => <li className="is-suppressed" data-suppressed="true" key={label}>
      <div><span>{label}</span><strong>{value}</strong></div>
      <small>隐私抑制值，不推断实际数量</small>
    </li>)}
  </ol>;
}


function LatencyVisual({ metric }: { metric: FaeReportMetric }) {
  const values = metric.value as Record<string, number | string>;
  const quantiles = ["p50", "p90", "p95"];
  return <div className="fae-report-latency">
    {quantiles.map((key) => typeof values[key] === "number"
      ? <div key={key}>{key.toUpperCase()} <strong>{(Number(values[key]) / 1000).toFixed(1)} 秒</strong></div>
      : null)}
  </div>;
}


export function ReportMetricVisual({ metric, presentation }: { metric: FaeReportMetric; presentation: MetricPresentation }) {
  return <article className={`fae-report-metric is-${presentation.kind}`} data-metric={metric.metric_id}>
    <header><span>{metric.label}</span>{presentation.note && <p>{presentation.note}</p>}</header>
    {presentation.kind === "ratio" ? <RatioVisual metric={metric} />
      : presentation.kind === "ranked_distribution" ? <DistributionVisual metric={metric} />
        : presentation.kind === "latency_quantiles" ? <LatencyVisual metric={metric} />
          : <strong className="fae-report-metric__value">{formatPublishedMetric(metric)}</strong>}
    <ReportMethodology metric={metric} />
  </article>;
}
