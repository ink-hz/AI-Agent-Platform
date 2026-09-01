import { metricsForChapter } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { ReportMetricVisual } from "./ReportMetricVisual";


const GROUPS = [
  ["quality", "独立复审结果", "仅陈述已发布比例与样本，不追加自定义达标结论"],
  ["reliability", "可靠性与响应", "负反馈、兜底与延迟的生产观测"],
  ["other", "其他已发布指标", "保留报告中新增且有效的效果指标"],
] as const;


export function AnswerEffectivenessChapter({ report }: { report: FaeAnalysisReport }) {
  const metrics = metricsForChapter(report, "answer_effectiveness");
  return <section className="fae-outcome-chapter" id="report-effectiveness" data-dimension="answer_effectiveness">
    <header className="fae-outcome-chapter__header"><p>03 · ANSWER EFFECTIVENESS</p><h2>回答效果</h2><span>展示真实复审样本、解决效果与运行可靠性。</span></header>
    {GROUPS.map(([group, title, description]) => {
      const selected = metrics.filter((item) => item.presentation.group === group);
      return selected.length ? <section className="fae-outcome-metric-group" data-metric-group={group} key={group}>
        <header><h3>{title}</h3><p>{description}</p></header>
        <div>{selected.map((item) => <ReportMetricVisual key={item.metric.metric_id} {...item} />)}</div>
      </section> : null;
    })}
  </section>;
}
