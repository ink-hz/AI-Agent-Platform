import { buildImprovementThemes } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { PlatformLink } from "../PlatformLink";


export function InsightAndImprovementChapter({ report }: { report: FaeAnalysisReport }) {
  const publishedThemes = buildImprovementThemes(report);
  const visibleThemes = publishedThemes.slice(0, 8);
  return <section className="fae-outcome-chapter" id="report-improvement" data-dimension="insights_improvement">
    <header className="fae-outcome-chapter__header"><p>04 · BUSINESS FEEDBACK</p><h2>业务反哺</h2><span>本期报告发布 {publishedThemes.length} 个需要跟进的主题。</span></header>
    <section className="fae-improvement-themes">
      {visibleThemes.length === 0 ? <p className="fae-improvement-themes__empty">本期未发布需要跟进的治理主题。</p>
        : <div>{visibleThemes.map(({ finding, recommendations }) => <article key={finding.finding_id}>
          <header><h4>{finding.title}</h4></header>
          <p>{finding.description}</p>
          {recommendations.slice(0, 1).map((item) => <section key={item.recommendation_id}><strong>{item.title}</strong><p>{item.proposed_action}</p><small>责任角色：{item.owner_role}</small></section>)}
          {recommendations.length === 0 && <p className="fae-improvement-themes__pending">改进动作与责任角色待发布</p>}
          <footer>{finding.linked_issue_ids.length > 0
            ? finding.linked_issue_ids.map((issueId) => <PlatformLink href={`/admin/fae/issues/${encodeURIComponent(issueId)}`} key={issueId}>查看修复闭环 →</PlatformLink>)
            : <span>待建立治理关联</span>}</footer>
        </article>)}</div>}
    </section>
  </section>;
}
