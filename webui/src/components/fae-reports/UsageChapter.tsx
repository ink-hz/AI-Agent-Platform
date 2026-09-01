import { metricById, metricPresentation, metricsByIds } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { ReportMetricVisual } from "./ReportMetricVisual";


const FACT_IDS = [
  "value.observed_included_sessions",
  "value.observed_included_turns",
  "value.observed_multiturn_sessions",
] as const;


export function UsageChapter({ report }: { report: FaeAnalysisReport }) {
  const facts = metricsByIds(report, FACT_IDS);
  const distribution = metricById(report, "product.family_counts_public");
  return <section className="fae-outcome-chapter" id="report-usage" data-dimension="usage">
    <header className="fae-outcome-chapter__header"><p>01 · USAGE</p><h2>使用情况</h2><span>{report.source.session_count} 个生产会话完成 {report.source.turn_count} 轮真实问答。</span></header>
    {facts.length > 0 && <div className="fae-outcome-chapter__facts">{facts.map((metric) => <ReportMetricVisual
      key={metric.metric_id}
      metric={metric}
      presentation={metricPresentation(metric)}
    />)}</div>}
    {distribution && <div className="fae-outcome-chapter__visual"><ReportMetricVisual
      metric={distribution}
      presentation={metricPresentation(distribution)}
    /></div>}
  </section>;
}
