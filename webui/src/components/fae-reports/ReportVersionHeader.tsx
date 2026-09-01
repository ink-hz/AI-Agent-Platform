import type { FaeAnalysisReport, FaeReportSummary } from "../../faeReportTypes";
import { PlatformLink } from "../PlatformLink";


const DAY = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});


export function ReportVersionHeader({ report, summaries = [] }: { report: FaeAnalysisReport; summaries?: FaeReportSummary[] }) {
  const versions = summaries
    .filter((item) => item.report_id === report.report_id)
    .sort((left, right) => right.report_version - left.report_version);
  return <div className="fae-outcome-version" aria-label="报告版本与周期">
    <div className="fae-outcome-version__identity">
      <div>
        <span>{report.report_type === "topic" ? "专题成果报告" : "周期成果报告"}</span>
        <strong>版本 {report.report_version}</strong>
        <b className={report.currentness === "source_updated" ? "is-updated" : "is-frozen"}>
          {report.currentness === "source_updated" ? "生产数据已有更新" : "冻结报告"}
        </b>
      </div>
      {report.currentness === "source_updated" && <PlatformLink className="fae-outcome-currentness-link" href="/admin/fae">查看最新运营数据 →</PlatformLink>}
    </div>
    <dl>
      <div><dt>统计周期</dt><dd>{DAY.format(new Date(report.period.start_at))}—{DAY.format(new Date(report.period.end_at))}</dd></div>
      <div><dt>数据截止</dt><dd>{DAY.format(new Date(report.data_cutoff_at))}</dd></div>
    </dl>
    {versions.length > 0 && <nav className="fae-outcome-version-index" aria-label="报告版本">
      {versions.map((item) => <PlatformLink
        aria-current={item.report_version === report.report_version ? "page" : undefined}
        className={item.report_version === report.report_version ? "is-current" : undefined}
        href={`/admin/fae/reports/${encodeURIComponent(item.report_id)}?version=${item.report_version}`}
        key={`${item.report_id}:${item.report_version}`}
      >版本 {item.report_version}{item.status === "failed" ? " · 发布失败" : ""}</PlatformLink>)}
    </nav>}
  </div>;
}
