import { metricsByIds, metricPresentation } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { ReportMetricVisual } from "./ReportMetricVisual";


const VALUE_IDS = [
  "value.assisted_reviewed_sessions",
  "value.scenario_potential_conversion_sessions",
] as const;


export function BusinessValueChapter({ report }: { report: FaeAnalysisReport }) {
  const values = metricsByIds(report, VALUE_IDS);
  return <section className="fae-outcome-chapter" id="report-value" data-dimension="business_value">
    <header className="fae-outcome-chapter__header"><p>02 · BUSINESS VALUE</p><h2>业务价值</h2><span>本期成果只统计经过独立复审确认的价值，潜在机会单独呈现。</span></header>
    {values.length > 0 && <div className="fae-outcome-value-comparison">{values.map((metric) => <ReportMetricVisual
      key={metric.metric_id}
      metric={metric}
      presentation={metricPresentation(metric)}
    />)}</div>}
    <p className="fae-outcome-value-note">潜在机会不计入已实现成果。</p>
    <section className="fae-outcome-cases"><header><p>APPROVED CASES</p><h3>典型业务案例</h3></header>
      {report.cases.length === 0 ? <p className="fae-outcome-cases__pending">典型案例待业务批准</p>
        : <div>{report.cases.map((item) => <article key={item.case_id}><h4>{item.title}</h4><p>{item.scenario}</p><strong>{item.outcome}</strong></article>)}</div>}
    </section>
  </section>;
}
