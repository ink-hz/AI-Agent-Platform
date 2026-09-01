import { metricPresentation, metricsByIds } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { ReportMetricVisual } from "./ReportMetricVisual";


const EFFECT_IDS = [
  "quality.reviewed_count",
  "quality.reviewed_fully_resolved_rate",
  "quality.reviewed_first_turn_resolution_rate",
  "feedback.bad_affected_sessions",
] as const;


export function AnswerEffectivenessChapter({ report }: { report: FaeAnalysisReport }) {
  const metrics = metricsByIds(report, EFFECT_IDS);
  return <section className="fae-outcome-chapter" id="report-effectiveness" data-dimension="answer_effectiveness">
    <header className="fae-outcome-chapter__header"><p>03 · ANSWER EFFECTIVENESS</p><h2>回答效果</h2><span>{report.source.reviewed_session_count} / {report.source.session_count} 个生产会话已完成独立复审。</span></header>
    {metrics.length > 0 && <div className="fae-outcome-chapter__facts">{metrics.map((metric) => <ReportMetricVisual
      key={metric.metric_id}
      metric={metric}
      presentation={metricPresentation(metric)}
    />)}</div>}
  </section>;
}
