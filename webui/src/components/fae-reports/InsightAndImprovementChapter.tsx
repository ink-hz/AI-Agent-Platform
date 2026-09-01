import { buildImprovementThemes, metricsForChapter } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { PlatformLink } from "../PlatformLink";
import { ReportMetricVisual } from "./ReportMetricVisual";


export function InsightAndImprovementChapter({ report }: { report: FaeAnalysisReport }) {
  const metrics = metricsForChapter(report, "insights_improvement");
  const themes = buildImprovementThemes(report);
  return <section className="fae-outcome-chapter" id="report-improvement" data-dimension="insights_improvement">
    <header className="fae-outcome-chapter__header"><p>04 · INSIGHTS &amp; IMPROVEMENT</p><h2>业务洞察与改进</h2><span>把生产信号、问题根因和改进行动放在同一条证据链上。</span></header>
    <section className="fae-outcome-metric-group"><header><h3>生产信号</h3><p>高频产品、场景与失败层均来自已发布统计。</p></header>
      <div>{metrics.map((item) => <ReportMetricVisual key={item.metric.metric_id} {...item} />)}</div>
    </section>
    <section className="fae-improvement-themes"><header><p>GOVERNED IMPROVEMENTS</p><h3>问题与改进成果</h3></header>
      {themes.length === 0 ? <p className="fae-improvement-themes__empty">本期未发布需要跟进的治理主题。</p>
        : <div>{themes.map(({ finding, recommendations }) => <article key={finding.finding_id}>
          <header><span className={`is-${finding.severity}`}>{finding.severity}</span><h4>{finding.title}</h4></header>
          <p>{finding.description}</p>
          <dl><dt>影响范围</dt><dd>{finding.impact_scope}</dd><dt>根因判断</dt><dd>{finding.root_cause_hypothesis}</dd></dl>
          {recommendations.map((item) => <section key={item.recommendation_id}><b>{item.priority.toUpperCase()}</b><div><strong>{item.title}</strong><p>{item.proposed_action}</p><small>责任角色：{item.owner_role}</small></div></section>)}
          <footer>{finding.linked_issue_ids.length > 0
            ? finding.linked_issue_ids.map((issueId) => <PlatformLink href={`/admin/fae/issues/${encodeURIComponent(issueId)}`} key={issueId}>查看修复闭环 →</PlatformLink>)
            : <span>待建立治理关联</span>}</footer>
        </article>)}</div>}
    </section>
  </section>;
}
