import type { FaeReportMetric } from "../../faeReportTypes";


const NUMBER = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });


export function ReportMethodology({ metric }: { metric: FaeReportMetric }) {
  const hasFraction = metric.numerator !== null && metric.denominator !== null;
  return <details className="fae-report-methodology">
    <summary>统计口径</summary>
    <dl>
      {hasFraction && <><dt>分子 / 分母</dt><dd>{NUMBER.format(metric.numerator!)} / {NUMBER.format(metric.denominator!)}</dd></>}
      {!hasFraction && metric.denominator !== null && <><dt>统计基数</dt><dd>{NUMBER.format(metric.denominator)}</dd></>}
      <dt>筛选条件</dt><dd>{metric.filters.length ? metric.filters.join(" · ") : "按报告发布口径"}</dd>
      {metric.assumptions.length > 0 && <><dt>假设</dt><dd>{metric.assumptions.join(" · ")}</dd></>}
      <dt>证据制品</dt><dd>{metric.evidence_artifact_refs.join(" · ")}</dd>
    </dl>
  </details>;
}
