import { metricsForChapter } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { ReportMetricVisual } from "./ReportMetricVisual";


function ValueGroup({ report, kind }: { report: FaeAnalysisReport; kind: "realized" | "potential" }) {
  const metrics = metricsForChapter(report, "business_value").filter((item) => item.presentation.group === kind);
  const realized = kind === "realized";
  return <section className={`fae-outcome-value-group is-${kind}`} data-value-kind={kind}>
    <header>
      <p>{realized ? "REALIZED VALUE" : "CONVERSION POTENTIAL"}</p>
      <h3>{realized ? "已实现的业务价值" : "可继续转化的增长潜力"}</h3>
      <span>{realized ? "仅呈现已经过独立复审确认的生产成果。" : "这是待进一步闭环的机会，不等于已经实现的业务价值。"}</span>
    </header>
    <div>{metrics.map((item) => <ReportMetricVisual key={item.metric.metric_id} {...item} />)}</div>
  </section>;
}


export function BusinessValueChapter({ report }: { report: FaeAnalysisReport }) {
  const other = metricsForChapter(report, "business_value").filter((item) => !["realized", "potential"].includes(item.presentation.group));
  return <section className="fae-outcome-chapter" id="report-value" data-dimension="business_value">
    <header className="fae-outcome-chapter__header"><p>02 · BUSINESS VALUE</p><h2>业务价值</h2><span>把已经实现的成绩与尚待转化的潜力分开说明。</span></header>
    <div className="fae-outcome-value-pair"><ValueGroup report={report} kind="realized" /><ValueGroup report={report} kind="potential" /></div>
    {other.length > 0 && <section className="fae-outcome-metric-group"><header><h3>其他已发布指标</h3></header><div>{other.map((item) => <ReportMetricVisual key={item.metric.metric_id} {...item} />)}</div></section>}
    <section className="fae-outcome-cases"><header><p>APPROVED CASES</p><h3>典型业务案例</h3></header>
      {report.cases.length === 0 ? <p className="fae-outcome-cases__pending">典型案例待业务批准</p>
        : <div>{report.cases.map((item) => <article key={item.case_id}><h4>{item.title}</h4><p>{item.scenario}</p><strong>{item.outcome}</strong></article>)}</div>}
    </section>
  </section>;
}
