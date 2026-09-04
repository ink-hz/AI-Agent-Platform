import { useEffect, useMemo, useState, type FormEvent } from "react";

import {
  listAccessEvents,
  listAccessSubjects,
  type AccessHistoryEvent,
  type AccessHistoryFilters,
  type AccessHistoryPageResult,
  type AccessHistorySubject,
  type AccessSubjectPageResult,
} from "../accessHistoryApi";

const SUBJECT_PAGE_SIZE = 20;
const EVENT_PAGE_SIZE = 100;
const WORKSPACES = [
  ["", "全部产品"], ["platform", "Agent Platform"], ["hr", "HR"],
  ["marketing", "Marketing"], ["office", "行政"], ["fae", "FAE"],
  ["voc", "VOC"], ["admin", "管理中心"],
] as const;
const PRODUCT_NAMES = Object.fromEntries(WORKSPACES.filter(([key]) => key)) as Record<string, string>;

function eventLabel(event: AccessHistoryEvent): string {
  if (event.event_kind === "page_view") return event.page_display_name ?? "页面访问";
  return event.login_kind === "in_client" ? "钉钉内登录" : "钉钉扫码登录";
}

function localTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(value));
}

function localDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "long", day: "numeric", weekday: "short",
  }).format(new Date(value));
}

function localDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value));
}

function exclusiveDateEnd(value: string): string {
  return new Date(new Date(`${value}T00:00:00+08:00`).getTime() + 86_400_000).toISOString();
}

function workspaceName(key: string | null): string {
  return key ? PRODUCT_NAMES[key] ?? key : "Agent Platform";
}

function departmentText(departments: readonly string[]): string {
  return departments.length ? departments.join(" · ") : "部门未同步";
}

function latestPath(subject: AccessHistorySubject): string {
  if (subject.latest_event_kind === "login_succeeded") return "企业登录";
  return [subject.latest_module_display_name, subject.latest_page_display_name].filter(Boolean).join(" · ") || "页面访问";
}

function Timeline({ result }: { result: AccessHistoryPageResult }) {
  const groups = useMemo(() => {
    const grouped = new Map<string, AccessHistoryEvent[]>();
    for (const event of result.items) {
      const date = localDate(event.occurred_at);
      grouped.set(date, [...(grouped.get(date) ?? []), event]);
    }
    return [...grouped.entries()];
  }, [result]);

  return <div className="access-timeline">
    {groups.map(([date, events]) => <section className="access-timeline-day" key={date}>
      <h3>{date}</h3>
      <ol>{events.map((event) => <li key={event.access_event_id}>
        <time dateTime={event.occurred_at}>{localTime(event.occurred_at)}</time>
        <span className={`access-event-dot is-${event.event_kind}`} aria-hidden="true" />
        <div>
          <strong>{eventLabel(event)}</strong>
          <p>{workspaceName(event.workspace_key)}<span> / </span>{event.module_display_name ?? "企业登录"}</p>
          {event.agent_id && <small>Agent · {event.agent_id}</small>}
        </div>
      </li>)}</ol>
    </section>)}
  </div>;
}

export function AccessHistoryPage({
  loadAccessSubjects = listAccessSubjects,
  loadAccessEvents = listAccessEvents,
}: {
  loadAccessSubjects?: (filters: AccessHistoryFilters, signal?: AbortSignal) => Promise<AccessSubjectPageResult>;
  loadAccessEvents?: (filters: AccessHistoryFilters, signal?: AbortSignal) => Promise<AccessHistoryPageResult>;
}) {
  const [displayName, setDisplayName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [eventKind, setEventKind] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [filters, setFilters] = useState<AccessHistoryFilters>({ limit: SUBJECT_PAGE_SIZE, offset: 0 });
  const [subjects, setSubjects] = useState<AccessSubjectPageResult | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<AccessHistoryPageResult | null>(null);
  const [timelineOffset, setTimelineOffset] = useState(0);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineFailed, setTimelineFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setFailed(false); setLoading(true);
    void loadAccessSubjects(filters, controller.signal).then((value) => {
      if (!controller.signal.aborted) setSubjects(value);
    }).catch(() => {
      if (!controller.signal.aborted) { setSubjects(null); setFailed(true); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, loadAccessSubjects]);

  useEffect(() => {
    if (!expandedName) { setTimeline(null); setTimelineFailed(false); return; }
    const controller = new AbortController();
    setTimeline(null); setTimelineFailed(false); setTimelineLoading(true);
    void loadAccessEvents({ ...filters, display_name: expandedName, limit: EVENT_PAGE_SIZE, offset: timelineOffset }, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setTimeline((current) => timelineOffset === 0 || !current
          ? value
          : { ...value, items: [...current.items, ...value.items] });
      })
      .catch(() => { if (!controller.signal.aborted) setTimelineFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setTimelineLoading(false); });
    return () => controller.abort();
  }, [expandedName, filters, loadAccessEvents, timelineOffset]);

  function submit(event: FormEvent) {
    event.preventDefault();
    setExpandedName(null);
    setFilters({
      limit: SUBJECT_PAGE_SIZE, offset: 0,
      ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
      ...(workspace ? { workspace_key: workspace } : {}),
      ...(eventKind === "login_succeeded" || eventKind === "page_view" ? { event_kind: eventKind } : {}),
      ...(dateFrom ? { date_from: `${dateFrom}T00:00:00+08:00` } : {}),
      ...(dateTo ? { date_to: exclusiveDateEnd(dateTo) } : {}),
    });
  }

  return <section className="access-history-page">
    <header className="access-history-heading"><p>OWNER AUDIT</p><h1>访问记录</h1><span>按企业花名查看登录和页面访问。部门来自当前钉钉通讯录，访问内容与业务参数不会被记录。</span></header>
    <form className="access-history-filters" onSubmit={submit}>
      <label>花名<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={128} placeholder="例如：苍渊" /></label>
      <label>产品<select value={workspace} onChange={(event) => setWorkspace(event.target.value)}>{WORKSPACES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label>类型<select value={eventKind} onChange={(event) => setEventKind(event.target.value)}><option value="">全部类型</option><option value="login_succeeded">登录</option><option value="page_view">页面访问</option></select></label>
      <label>开始日期<input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label>
      <label>结束日期<input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label>
      <button type="submit">查询</button>
    </form>
    {failed && <p className="auth-message is-error" role="alert">访问记录暂不可用，请稍后重试。</p>}
    {!failed && loading && <p className="access-history-state">正在读取人员访问记录…</p>}
    {!failed && !loading && subjects?.items.length === 0 && <p className="access-history-state">所选范围内暂无访问记录。</p>}
    {!failed && subjects && subjects.items.length > 0 && <div className="access-subject-list">
      {subjects.items.map((subject) => {
        const expanded = expandedName === subject.display_name;
        return <article className={`access-subject-card${expanded ? " is-expanded" : ""}`} key={subject.display_name}>
          <button className="access-subject-summary" type="button" aria-expanded={expanded} onClick={() => { setTimelineOffset(0); setExpandedName(expanded ? null : subject.display_name); }}>
            <span className="access-subject-avatar" aria-hidden="true">{subject.display_name.slice(0, 1)}</span>
            <span className="access-subject-identity"><strong>{subject.display_name}</strong><small>{departmentText(subject.departments)}</small></span>
            <span className="access-subject-count"><strong>{subject.event_count}</strong><small>次访问</small></span>
            <span className="access-subject-latest"><small>最近访问 · {localDateTime(subject.latest_occurred_at)}</small><strong>{workspaceName(subject.latest_workspace_key)} · {latestPath(subject)}</strong></span>
            <span className="access-subject-chevron" aria-hidden="true">⌄</span>
          </button>
          {expanded && <div className="access-subject-detail">
            {timelineLoading && <p className="access-history-state">正在读取该员工的访问明细…</p>}
            {timelineFailed && <p className="auth-message is-error" role="alert">该员工的访问明细暂不可用，请稍后重试。</p>}
            {!timelineLoading && !timelineFailed && timeline?.items.length === 0 && <p className="access-history-state">暂无访问明细。</p>}
            {!timelineFailed && timeline && <Timeline result={timeline} />}
            {!timelineFailed && timeline?.has_more && <button className="access-timeline-more" type="button" disabled={timelineLoading} onClick={() => setTimelineOffset((value) => value + EVENT_PAGE_SIZE)}>{timelineLoading ? "正在读取…" : "加载更早记录"}</button>}
          </div>}
        </article>;
      })}
    </div>}
    {!failed && subjects && <nav className="access-history-pagination" aria-label="人员访问记录分页">
      <button type="button" disabled={subjects.offset === 0 || loading} onClick={() => { setExpandedName(null); setFilters((current) => ({ ...current, offset: Math.max(0, (current.offset ?? 0) - SUBJECT_PAGE_SIZE) })); }}>上一页</button>
      <span>第 {Math.floor(subjects.offset / subjects.limit) + 1} 页</span>
      <button type="button" disabled={!subjects.has_more || loading} onClick={() => { setExpandedName(null); setFilters((current) => ({ ...current, offset: (current.offset ?? 0) + SUBJECT_PAGE_SIZE })); }}>下一页</button>
    </nav>}
  </section>;
}
