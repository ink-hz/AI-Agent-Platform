import type { FaeAnalysisReport } from "../../faeReportTypes";


const DAY = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});


export function ReportVersionHeader({ report }: { report: FaeAnalysisReport }) {
  return <div className="fae-outcome-version" aria-label="报告版本与周期">
    <div>
      <span>{report.report_type === "topic" ? "专题成果报告" : "周期成果报告"}</span>
      <strong>版本 {report.report_version}</strong>
      <b className={report.currentness === "source_updated" ? "is-updated" : "is-frozen"}>
        {report.currentness === "source_updated" ? "生产数据已有更新" : "冻结报告"}
      </b>
    </div>
    <dl>
      <div><dt>统计周期</dt><dd>{DAY.format(new Date(report.period.start_at))}—{DAY.format(new Date(report.period.end_at))}</dd></div>
      <div><dt>数据截止</dt><dd>{DAY.format(new Date(report.data_cutoff_at))}</dd></div>
    </dl>
  </div>;
}
