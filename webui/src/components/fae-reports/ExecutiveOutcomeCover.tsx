import { formatPublishedMetric, metricById, reviewCoverage } from "../../faeReportPresentation";
import type { FaeAnalysisReport, FaeReportMetric, FaeReportSummary } from "../../faeReportTypes";
import { ReportVersionHeader } from "./ReportVersionHeader";


const NUMBER = new Intl.NumberFormat("zh-CN");

function publishedOrUnavailable(metric: FaeReportMetric | undefined): string {
  return metric ? formatPublishedMetric(metric) : "暂未发布";
}


export function ExecutiveOutcomeCover({ report, summaries }: { report: FaeAnalysisReport; summaries?: FaeReportSummary[] }) {
  const coverage = reviewCoverage(report);
  const realized = metricById(report, "value.assisted_reviewed_sessions");
  const potential = metricById(report, "value.scenario_potential_conversion_sessions");
  return <header className="fae-outcome-cover" data-report-cover id="report-cover">
    <ReportVersionHeader report={report} summaries={summaries} />
    <div className="fae-outcome-cover__lead">
      <p className="fae-outcome-kicker">FAE Agent 生产成果</p>
      <h1>{report.summary?.headline ?? report.title}</h1>
      {report.summary?.overview && <span>{report.summary.overview}</span>}
    </div>
    <div className="fae-outcome-cover__facts">
      <article>
        <span>生产会话</span>
        <strong>{NUMBER.format(report.source.session_count)}</strong>
        <small>{NUMBER.format(report.source.turn_count)} 轮真实问答</small>
      </article>
      <article>
        <span>独立复审</span>
        <strong>{NUMBER.format(coverage.numerator)} / {NUMBER.format(coverage.denominator)}</strong>
        <small>{(coverage.ratio * 100).toFixed(1)}% 覆盖</small>
      </article>
      <article data-outcome="realized">
        <span>已确认价值</span>
        <strong>{publishedOrUnavailable(realized)}</strong>
        <small>只计入经过复审的成果</small>
      </article>
      <article data-outcome="potential">
        <span>潜在机会</span>
        <strong>{publishedOrUnavailable(potential)}</strong>
        <small>不计入已实现成果</small>
      </article>
    </div>
  </header>;
}
