import { useEffect, useState } from "react";

import { ErrorState, LoadingState } from "../components/DataState";
import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { PlatformLink } from "../components/PlatformLink";
import { faeReportApi } from "../faeReportApi";
import type { FaeAnalysisReport, FaeReportDimension, FaeReportMetric } from "../faeReportTypes";


const DIMENSIONS: Array<{ key: FaeReportDimension; eyebrow: string; title: string }> = [
  { key: "usage", eyebrow: "USAGE", title: "使用情况" },
  { key: "business_value", eyebrow: "BUSINESS VALUE", title: "业务价值" },
  { key: "answer_effectiveness", eyebrow: "ANSWER EFFECTIVENESS", title: "回答效果" },
  { key: "insights_improvement", eyebrow: "INSIGHTS & IMPROVEMENT", title: "业务洞察与改进" },
];

const DATE_TIME = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function metricValue(metric: FaeReportMetric): string {
  if (typeof metric.value === "object") return Object.entries(metric.value).map(([key, value]) => `${key} ${value}`).join(" · ");
  if (metric.unit === "ratio") return `${(metric.value * 100).toFixed(1)}%`;
  if (metric.unit === "percent") return `${metric.value.toFixed(1)}%`;
  if (metric.unit === "milliseconds") return `${Math.round(metric.value)} ms`;
  return new Intl.NumberFormat("zh-CN").format(metric.value);
}

function denominator(metric: FaeReportMetric): string {
  if (metric.denominator !== null) return `分母 ${metric.denominator}${metric.numerator !== null ? ` · 分子 ${metric.numerator}` : ""}`;
  return metric.filters.length ? metric.filters.join(" · ") : "按已发布统计口径";
}

function HeadlineCards({ report }: { report: FaeAnalysisReport }) {
  const byId = new Map(report.metrics.map((metric) => [metric.metric_id, metric]));
  const cards: Array<[string, string, string]> = [
    ["累计服务规模", `${report.source.session_count} Sessions`, `${report.source.turn_count} 个问答 Turn`],
    ["复杂业务承接", byId.has("value.observed_multiturn_sessions") ? metricValue(byId.get("value.observed_multiturn_sessions")!) : `${report.source.reviewed_session_count} 个已复审 Session`, "以多轮与复审样本为证据"],
    ["已形成业务价值", byId.has("value.assisted_reviewed_sessions") ? metricValue(byId.get("value.assisted_reviewed_sessions")!) : `${report.source.reviewed_session_count}`, "真实观察，不包含潜在估算"],
    ["增长潜力", byId.has("value.scenario_potential_conversion_sessions") ? metricValue(byId.get("value.scenario_potential_conversion_sessions")!) : "待持续验证", "潜在价值，与已实现价值分开"],
  ];
  return <section className="fae-report-hero__cards" aria-label="成果摘要">{cards.map(([label, value, detail]) => <article key={label}>
    <span>{label}</span><strong>{value}</strong><small>{detail}</small>
  </article>)}</section>;
}

function DimensionSection({ report, dimension, eyebrow, title }: { report: FaeAnalysisReport; dimension: FaeReportDimension; eyebrow: string; title: string }) {
  const metrics = report.metrics.filter((metric) => metric.dimension === dimension);
  const findings = report.findings.filter((finding) => finding.dimension === dimension);
  const recommendations = report.recommendations.filter((item) => item.dimension === dimension);
  return <section className="fae-report-dimension" data-dimension={dimension}>
    <header><p>{eyebrow}</p><h2>{title}</h2></header>
    <div className="fae-report-metrics">{metrics.map((metric) => <article data-metric={metric.metric_id} key={metric.metric_id}>
      <span>{metric.label}</span><strong>{metricValue(metric)}</strong><small>{denominator(metric)}</small>
    </article>)}</div>
    {findings.length > 0 && <div className="fae-report-findings">{findings.map((finding) => <article key={finding.finding_id}>
      <div><span className={`is-${finding.severity}`}>{finding.severity}</span><h3>{finding.title}</h3></div>
      <p>{finding.description}</p><dl><dt>影响范围</dt><dd>{finding.impact_scope}</dd><dt>根因判断</dt><dd>{finding.root_cause_hypothesis}</dd></dl>
      {finding.linked_issue_ids.length > 0 && <div className="fae-report-links">{finding.linked_issue_ids.map((issueId) => <PlatformLink key={issueId} href={`/admin/fae/issues/${encodeURIComponent(issueId)}`}>查看修复闭环 →</PlatformLink>)}</div>}
    </article>)}</div>}
    {recommendations.length > 0 && <div className="fae-report-recommendations"><h3>下一步建议</h3>{recommendations.map((item) => <article key={item.recommendation_id}><b>{item.priority.toUpperCase()}</b><div><strong>{item.title}</strong><p>{item.proposed_action}</p><small>责任角色：{item.owner_role}</small></div></article>)}</div>}
  </section>;
}

function Report({ report }: { report: FaeAnalysisReport }) {
  if (report.status === "failed") return <section className="fae-workbench__empty" role="alert"><h2>报告发布失败</h2><p>{report.failure?.message ?? "本次分析未通过发布门禁。"}</p></section>;
  return <article className="fae-report" data-report-id={report.report_id}>
    <header className="fae-report-hero">
      <div><p>AI FAE PRODUCTION OUTCOME</p><h1>{report.title}</h1><strong>{report.summary?.headline}</strong><span>{report.summary?.overview}</span></div>
      <aside className={report.currentness === "source_updated" ? "is-stale" : "is-current"}>
        <b>{report.currentness === "source_updated" ? "数据已有更新" : "冻结报告"}</b>
        <span>数据截止 {DATE_TIME.format(new Date(report.data_cutoff_at))}</span>
        <span>生成于 {DATE_TIME.format(new Date(report.generated_at))}</span>
      </aside>
    </header>
    <HeadlineCards report={report} />
    {DIMENSIONS.map((item) => <DimensionSection key={item.key} report={report} dimension={item.key} eyebrow={item.eyebrow} title={item.title} />)}
    <section className="fae-report-cases"><p>BUSINESS CASES</p><h2>典型案例</h2>{report.cases.length === 0
      ? <div className="fae-report-cases__pending">典型案例待业务批准</div>
      : report.cases.map((item) => <article key={item.case_id}><h3>{item.title}</h3><p>{item.scenario}</p><strong>{item.outcome}</strong></article>)}</section>
  </article>;
}

export function FaeReportsPage({ reportId }: { reportId?: string }) {
  const [report, setReport] = useState<FaeAnalysisReport | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setReport(null); setFailed(false);
    const request = reportId ? faeReportApi.detail(reportId, controller.signal) : faeReportApi.latest(controller.signal);
    void request.then((value) => { if (!controller.signal.aborted) setReport(value); }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [reportId, attempt]);
  return <FaeWorkbenchShell currentSection="reports">{failed
    ? <ErrorState onRetry={() => setAttempt((value) => value + 1)} />
    : report ? <Report report={report} /> : <LoadingState label="正在读取 FAE 分析报告" />}</FaeWorkbenchShell>;
}
