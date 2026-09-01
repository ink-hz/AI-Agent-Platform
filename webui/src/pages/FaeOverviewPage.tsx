import { useEffect, useState, type CSSProperties } from "react";

import { ErrorState, LoadingState } from "../components/DataState";
import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { PlatformLink } from "../components/PlatformLink";
import { useDeploymentContext } from "../deploymentContext";
import { faeWorkbenchApi } from "../faeWorkbenchApi";
import type { FaeOverview, FaeSessionAttention, FaeSummary, FaeTrendPoint } from "../faeWorkbenchTypes";


const SHANGHAI_TIME = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const DAY = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
});

const ISSUE_STATUS_LABELS: Record<string, string> = {
  pending_triage: "待归因",
  fixing: "修复中",
  awaiting_merge: "待合并",
  awaiting_deploy: "待部署",
  awaiting_replay: "待复跑",
  awaiting_review: "待语义复审",
};

const CLOSED_ISSUE_STATUSES = new Set(["closed", "duplicate", "not_actionable", "wont_fix"]);
const LOCAL_ISSUE_STATUSES = new Set(Object.keys(ISSUE_STATUS_LABELS));
const CLOUD_ISSUE_DISPOSITIONS = new Set(["actionable", "duplicate", "not_actionable", "wont_fix"]);

const ATTENTION_REASON_LABELS: Record<FaeSessionAttention["reason"], string> = {
  fallback: "触发回退",
  failed_outcome: "结果异常",
  empty_answer: "回答为空",
};


function timeParts(value: string): Record<string, string> {
  return Object.fromEntries(SHANGHAI_TIME.formatToParts(new Date(value)).map((part) => [part.type, part.value]));
}


function formatTime(value: string): string {
  const parts = timeParts(value);
  return `${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}`;
}


function formatDay(value: string): string {
  const parts = Object.fromEntries(DAY.formatToParts(new Date(`${value}T00:00:00+08:00`)).map((part) => [part.type, part.value]));
  return `${parts.month}月${parts.day}日`;
}


function MetricCard({
  metric,
  label,
  value,
  detail,
  href,
}: {
  metric: string;
  label: string;
  value: string | null;
  detail: string;
  href?: string;
}) {
  const body = <>
    <span>{label}</span>
    <strong>{value ?? "暂不可用"}</strong>
    <small>{detail}</small>
    {value !== null && href && <span className="fae-summary-card__action">查看详情 →</span>}
  </>;
  return <article className={`fae-summary-card${value === null ? " is-unavailable" : href ? "" : " is-static"}`} data-metric={metric}>
    {value === null || !href ? body : <PlatformLink className="fae-summary-card__link" href={href}>{body}</PlatformLink>}
  </article>;
}


function sessionsHref(overview: FaeOverview, filters: Array<[string, string]> = []): string {
  const query = new URLSearchParams([
    ...filters,
    ["date_from", overview.period_start],
    ["date_before", overview.period_end],
  ]);
  return `/admin/fae/sessions?${query}`;
}


function Summary({ overview, summary, cloudReplica }: { overview: FaeOverview; summary: FaeSummary; cloudReplica: boolean }) {
  const periodHref = sessionsHref(overview);
  return <section className="fae-overview__summary" aria-label="运营摘要">
    <MetricCard metric="sessions" label="Session" value={`${summary.session_count} 个 Session`} detail="本统计周期内" href={periodHref} />
    <MetricCard metric="active-subjects" label="活跃主体" value={`${summary.active_subject_count} 个活跃主体`} detail="有可识别主体的 Session" href={sessionsHref(overview, [["has_subject", "true"]])} />
    <MetricCard metric="negative-turns" label="负向 Turn" value={`${summary.negative_turn_count} 个负向 Turn`} detail={`${summary.negative_feedback_events} 条负向反馈`} href={sessionsHref(overview, [["sentiment", "negative"]])} />
    <MetricCard metric="abnormal-sessions" label="异常 Session" value={`${summary.abnormal_session_count} 个异常 Session`} detail="空回答、fallback 或失败结果" href={sessionsHref(overview, [["abnormal", "true"]])} />
    <MetricCard metric="open-issues" label="开放 Issue" value={summary.open_issue_count === null ? null : `${summary.open_issue_count} 个开放 Issue`} detail="尚未完成闭环" href="/admin/fae/issues?status=open" />
    <MetricCard metric="p95-latency" label="响应耗时" value={summary.p95_duration_ms === null ? null : `p95 ${summary.p95_duration_ms} ms`} detail="有耗时样本的 Session Turn" href={sessionsHref(overview, [["has_latency", "true"]])} />
  </section>;
}


function IssueQueue({ overview, cloudReplica }: { overview: FaeOverview; cloudReplica: boolean }) {
  if (overview.issues.state.status === "unavailable") {
    return <section className="fae-overview-panel fae-overview-panel--unavailable" aria-labelledby="fae-issues-heading">
      <h2 id="fae-issues-heading">反馈与修复暂不可用</h2>
      <p>Issue 数据源当前不可用，Session 运营数据仍可继续查看。</p>
      <PlatformLink href="/admin/fae/issues">打开反馈与修复</PlatformLink>
    </section>;
  }
  const actionable = Object.entries(overview.issues.statuses)
    .filter(([status, count]) => count > 0 && (cloudReplica
      ? CLOUD_ISSUE_DISPOSITIONS.has(status) && !CLOSED_ISSUE_STATUSES.has(status)
      : LOCAL_ISSUE_STATUSES.has(status) && !CLOSED_ISSUE_STATUSES.has(status)))
    .sort(([left], [right]) => Object.keys(ISSUE_STATUS_LABELS).indexOf(left) - Object.keys(ISSUE_STATUS_LABELS).indexOf(right));
  return <section className="fae-overview-panel" aria-labelledby="fae-issues-heading">
    <header><div><p>FEEDBACK TO FIX</p><h2 id="fae-issues-heading">反馈与修复</h2></div><PlatformLink href="/admin/fae/issues">查看全部</PlatformLink></header>
    {actionable.length === 0
      ? <p className="fae-overview-panel__empty">当前没有开放 Issue。</p>
      : <ul className="fae-overview-list">{actionable.map(([status, count]) => <li key={status}>
        <PlatformLink href={`/admin/fae/issues?${cloudReplica ? "disposition" : "status"}=${encodeURIComponent(status)}`}><span>{ISSUE_STATUS_LABELS[status] ?? (status === "actionable" ? "需处理" : status)} </span><strong>{count}</strong></PlatformLink>
      </li>)}</ul>}
  </section>;
}


function AttentionQueue({ overview }: { overview: FaeOverview }) {
  if (overview.attention.state.status === "unavailable") {
    return <section className="fae-overview-panel fae-overview-panel--unavailable" aria-labelledby="fae-attention-heading">
      <h2 id="fae-attention-heading">异常 Session 暂不可用</h2>
      <p>异常 Session 聚合暂时无法读取。</p>
    </section>;
  }
  return <section className="fae-overview-panel" aria-labelledby="fae-attention-heading">
    <header><div><p>SESSION ATTENTION</p><h2 id="fae-attention-heading">异常 Sessions</h2></div></header>
    {overview.attention.items.length === 0
      ? <p className="fae-overview-panel__empty">当前没有需要关注的异常 Session。</p>
      : <ul className="fae-overview-list fae-attention-list">{overview.attention.items.map((item) => <li key={item.session_key}>
        <PlatformLink href={`/admin/fae/sessions/${encodeURIComponent(item.session_key)}`}>
          <span><strong>{item.title || "未命名 Session"}</strong><small>{ATTENTION_REASON_LABELS[item.reason]}</small></span>
          <time dateTime={item.last_active_at}>{formatTime(item.last_active_at)}</time>
        </PlatformLink>
      </li>)}</ul>}
  </section>;
}


function TrendBar({ point, value, kind, maximum }: { point: FaeTrendPoint; value: number; kind: "sessions" | "negative"; maximum: number }) {
  const series = kind === "sessions" ? "Session" : "负向 Turn";
  const style = { "--fae-bar-height": `${Math.max(value === 0 ? 0 : 8, (value / maximum) * 100)}%` } as CSSProperties;
  return <span className={`fae-trend-bar is-${kind}`} aria-label={`${formatDay(point.day)} ${series} ${value}`} role="img" style={style}>
    <span className="fae-trend-bar__value">{value}</span>
  </span>;
}


function Trends({ overview }: { overview: FaeOverview }) {
  if (overview.trends.state.status === "unavailable") {
    return <section className="fae-trends fae-overview-panel--unavailable" aria-labelledby="fae-trends-heading">
      <h2 id="fae-trends-heading">7 日趋势暂不可用</h2>
      <p>趋势聚合暂时无法读取，其余可用分区仍保留展示。</p>
    </section>;
  }
  const maximum = Math.max(1, ...overview.trends.points.flatMap((point) => [point.sessions, point.negative_turns]));
  return <section className="fae-trends" aria-labelledby="fae-trends-heading">
    <header><div><p>SEVEN DAY TREND</p><h2 id="fae-trends-heading">7 日趋势</h2></div><div className="fae-trend-legend"><span className="is-sessions">Session</span><span className="is-negative">负向 Turn</span></div></header>
    {overview.trends.points.length === 0
      ? <p className="fae-overview-panel__empty">当前周期暂无趋势数据。</p>
      : <ul className="fae-trend-chart">{overview.trends.points.map((point) => <li key={point.day}>
        <div className="fae-trend-bars">
          <TrendBar point={point} value={point.sessions} kind="sessions" maximum={maximum} />
          <TrendBar point={point} value={point.negative_turns} kind="negative" maximum={maximum} />
        </div>
        <time dateTime={point.day}>{formatDay(point.day)}</time>
      </li>)}</ul>}
  </section>;
}


function OverviewContent({ overview, cloudReplica }: { overview: FaeOverview; cloudReplica: boolean }) {
  const stale = overview.freshness.status === "stale";
  return <section className="fae-overview" aria-labelledby="fae-overview-heading">
    <header className="fae-overview__header"><div><p>OPERATIONAL OVERVIEW</p><h2 id="fae-overview-heading">FAE 运营概览</h2></div><p>围绕真实 Session、负向反馈与治理闭环查看最近七天运营状态。</p></header>
    <aside className={`fae-overview__freshness is-${overview.freshness.status}`} aria-label="数据新鲜度" role="status">
      <span><strong>统计周期</strong> <time dateTime={overview.period_start}>{formatTime(overview.period_start)}</time> 至 <time dateTime={overview.period_end}>{formatTime(overview.period_end)}</time></span>
      <span><strong>数据截止</strong> {overview.freshness.data_as_of
        ? <time dateTime={overview.freshness.data_as_of}>{formatTime(overview.freshness.data_as_of)}</time>
        : "暂不可用"}</span>
      <b>{stale ? "数据已过期" : overview.freshness.status === "fresh" ? "数据已同步" : "等待首次同步"}</b>
    </aside>
    {overview.summary.state.status === "available" && overview.summary.data
      ? <Summary overview={overview} summary={overview.summary.data} cloudReplica={cloudReplica} />
      : <section className="fae-overview__summary-unavailable" role="status"><h2>运营摘要暂不可用</h2><p>Session 聚合当前不可用，其他独立分区仍可继续查看。</p></section>}
    <div className="fae-overview__queues"><IssueQueue overview={overview} cloudReplica={cloudReplica} /><AttentionQueue overview={overview} /></div>
    <Trends overview={overview} />
    <section className="fae-report-preview" aria-labelledby="fae-reports-heading">
      {overview.reports.state.status === "available" ? <>
        <div><p>ANALYSIS REPORTS</p><h2 id="fae-reports-heading">{overview.reports.title}</h2><span>{overview.reports.currentness === "source_updated" ? "数据已有更新 · " : "冻结成果报告 · "}截止 {overview.reports.data_cutoff_at ? formatTime(overview.reports.data_cutoff_at) : "待确认"}</span></div>
        <PlatformLink href={`/admin/fae/reports/${encodeURIComponent(overview.reports.report_id!)}`}>查看完整成果 →</PlatformLink>
      </> : <>
        <div><p>ANALYSIS REPORTS</p><h2 id="fae-reports-heading">分析报告暂不可用</h2><span>未使用演示数据，等待真实 FAE 报告同步。</span></div>
        <PlatformLink href="/admin/fae/reports">查看报告 →</PlatformLink>
      </>}
    </section>
  </section>;
}


export function FaeOverviewPage() {
  const { deployment, resolved: deploymentResolved } = useDeploymentContext();
  const [overview, setOverview] = useState<FaeOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setOverview(null);
    setFailed(false);
    void faeWorkbenchApi.overview(controller.signal).then((value) => {
      if (!controller.signal.aborted) setOverview(value);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [attempt]);

  let content;
  if (failed) content = <ErrorState onRetry={() => setAttempt((value) => value + 1)} />;
  else if (!overview || !deploymentResolved) content = <LoadingState label="正在加载 FAE 运营概览" />;
  else content = <OverviewContent overview={overview} cloudReplica={deployment?.mode === "cloud-replica" && deployment.read_only} />;

  return <FaeWorkbenchShell currentSection="overview">{content}</FaeWorkbenchShell>;
}
