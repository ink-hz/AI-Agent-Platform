import { formatPublishedMetric, reviewCoverage } from "../../faeReportPresentation";
import type { FaeAnalysisReport, FaeReportMetric, FaeReportSummary } from "../../faeReportTypes";
import { ReportVersionHeader } from "./ReportVersionHeader";


const NUMBER = new Intl.NumberFormat("zh-CN");

function metric(report: FaeAnalysisReport, metricId: string): FaeReportMetric | undefined {
  return report.metrics.find((item) => item.metric_id === metricId);
}

function PublishedValue({ report, metricId }: { report: FaeAnalysisReport; metricId: string }) {
  const selected = metric(report, metricId);
  return <strong>{selected ? formatPublishedMetric(selected) : "暂未发布"}</strong>;
}


export function ExecutiveOutcomeCover({ report, summaries }: { report: FaeAnalysisReport; summaries?: FaeReportSummary[] }) {
  const coverage = reviewCoverage(report);
  const complexity = [
    ["value.observed_multiturn_sessions", "多轮会话"],
    ["value.observed_attachment_sessions", "图片或附件会话"],
    ["value.observed_non_work_hour_sessions", "非工作时段会话"],
  ] as const;
  const hasComplexity = complexity.some(([id]) => metric(report, id));
  return <header className="fae-outcome-cover" data-report-cover id="report-cover">
    <ReportVersionHeader report={report} summaries={summaries} />
    <div className="fae-outcome-cover__lead">
      <div>
        <p className="fae-outcome-kicker">FAE Agent 生产成果</p>
        <h1>FAE Agent 已经在真实生产中形成规模，<br />并产生了可量化、可审计的业务价值。</h1>
        <span>{report.summary?.overview}</span>
      </div>
      <div className="fae-outcome-scale" aria-label="生产服务规模">
        <strong>{NUMBER.format(report.source.session_count)}</strong>
        <span>个生产 Session</span>
        <small>完成 {NUMBER.format(report.source.turn_count)} 轮真实业务问答</small>
      </div>
    </div>
    <section className="fae-outcome-trust" aria-label="独立复审覆盖">
      <div><span>独立复审覆盖</span><strong>{NUMBER.format(coverage.numerator)} / {NUMBER.format(coverage.denominator)}</strong></div>
      <b>{(coverage.ratio * 100).toFixed(1)}%</b>
      <p>成果结论以独立复审样本为依据，不使用未经验证的价值估算。</p>
    </section>
    <div className="fae-outcome-evidence">
      <section className="fae-outcome-complexity" aria-label="复杂业务承接">
        <p>复杂业务承接</p>
        {hasComplexity ? <dl>{complexity.map(([id, label]) => {
          const selected = metric(report, id);
          return selected ? <div key={id}><dt>{label}</dt><dd>{formatPublishedMetric(selected)}</dd></div> : null;
        })}</dl> : <span>当前报告未发布复杂度拆分。</span>}
      </section>
      <section className="fae-outcome-value is-realized" data-outcome="realized">
        <p>已实现价值</p>
        <PublishedValue report={report} metricId="value.assisted_reviewed_sessions" />
        <span>经独立复审确认已形成 FAE 辅助价值</span>
      </section>
      <section className="fae-outcome-value is-potential" data-outcome="potential">
        <p>增长潜力</p>
        <PublishedValue report={report} metricId="value.scenario_potential_conversion_sessions" />
        <span>尚未完全闭环，不计入已实现成绩</span>
      </section>
    </div>
  </header>;
}
