import { metricsForChapter } from "../../faeReportPresentation";
import type { FaeAnalysisReport } from "../../faeReportTypes";
import { ReportMetricVisual } from "./ReportMetricVisual";


const GROUPS = [
  ["depth", "服务规模与深度", "真实生产会话与问答规模"],
  ["complexity", "复杂业务承接", "多轮、附件与非工作时段服务"],
  ["products", "高频产品", "报告周期内被咨询的产品族"],
  ["capabilities", "高频需求", "同一会话可包含多个需求标签"],
  ["other", "其他已发布指标", "保留报告中新增且有效的使用指标"],
] as const;


export function UsageChapter({ report }: { report: FaeAnalysisReport }) {
  const metrics = metricsForChapter(report, "usage");
  return <section className="fae-outcome-chapter" id="report-usage" data-dimension="usage">
    <header className="fae-outcome-chapter__header"><p>01 · USAGE</p><h2>使用情况</h2><span>回答“被用了多少、承接了怎样的真实工作”。</span></header>
    {GROUPS.map(([group, title, description]) => {
      const selected = metrics.filter((item) => item.presentation.group === group);
      return selected.length ? <section className="fae-outcome-metric-group" data-metric-group={group} key={group}>
        <header><h3>{title}</h3><p>{description}</p></header>
        <div>{selected.map((item) => <ReportMetricVisual key={item.metric.metric_id} {...item} />)}</div>
      </section> : null;
    })}
  </section>;
}
