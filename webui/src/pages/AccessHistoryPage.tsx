import { useEffect, useState, type FormEvent } from "react";

import { listAccessEvents, type AccessHistoryEvent, type AccessHistoryFilters, type AccessHistoryPageResult } from "../accessHistoryApi";

const PAGE_SIZE = 50;
const WORKSPACES = [
  ["", "全部页面"], ["platform", "Agent Platform"], ["hr", "HR"],
  ["marketing", "Marketing"], ["office", "行政"], ["fae", "FAE"],
  ["voc", "VOC"], ["admin", "管理中心"],
] as const;

function eventLabel(event: AccessHistoryEvent): string {
  if (event.event_kind === "page_view") return event.page_display_name ?? "页面访问";
  return event.login_kind === "in_client" ? "钉钉内登录" : "钉钉扫码登录";
}

function eventKindLabel(event: AccessHistoryEvent): string {
  return event.event_kind === "login_succeeded" ? "登录" : "页面访问";
}

function localTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(value));
}

export function AccessHistoryPage({
  loadAccessEvents = listAccessEvents,
}: {
  loadAccessEvents?: (filters: AccessHistoryFilters, signal?: AbortSignal) => Promise<AccessHistoryPageResult>;
}) {
  const [displayName, setDisplayName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [eventKind, setEventKind] = useState("");
  const [filters, setFilters] = useState<AccessHistoryFilters>({ limit: PAGE_SIZE, offset: 0 });
  const [result, setResult] = useState<AccessHistoryPageResult | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setFailed(false); setLoading(true);
    void loadAccessEvents(filters, controller.signal).then((value) => {
      if (!controller.signal.aborted) setResult(value);
    }).catch(() => {
      if (!controller.signal.aborted) { setResult(null); setFailed(true); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, loadAccessEvents]);

  function submit(event: FormEvent) {
    event.preventDefault();
    setFilters({
      limit: PAGE_SIZE, offset: 0,
      ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
      ...(workspace ? { workspace_key: workspace } : {}),
      ...(eventKind === "login_succeeded" || eventKind === "page_view" ? { event_kind: eventKind } : {}),
    });
  }

  return <section className="access-history-page">
    <header className="access-history-heading"><p>OWNER AUDIT</p><h1>登录与页面访问</h1><span>查看企业账号何时登录，以及访问了哪个产品页面。仅记录页面类别，不记录业务内容。</span></header>
    <form className="access-history-filters" onSubmit={submit}>
      <label>花名<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={128} placeholder="例如：苍渊" /></label>
      <label>产品<select value={workspace} onChange={(event) => setWorkspace(event.target.value)}>{WORKSPACES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label>类型<select value={eventKind} onChange={(event) => setEventKind(event.target.value)}><option value="">全部类型</option><option value="login_succeeded">登录</option><option value="page_view">页面访问</option></select></label>
      <button type="submit">查询</button>
    </form>
    {failed && <p className="auth-message is-error" role="alert">访问记录暂不可用，请稍后重试。</p>}
    {!failed && loading && <p className="access-history-state">正在读取访问记录…</p>}
    {!failed && !loading && result?.items.length === 0 && <p className="access-history-state">暂无访问记录。</p>}
    {!failed && result && result.items.length > 0 && <div className="access-history-table-wrap"><table className="access-history-table">
      <thead><tr><th>花名</th><th>时间</th><th>类型</th><th>页面 / 登录方式</th><th>Agent</th></tr></thead>
      <tbody>{result.items.map((event) => <tr key={event.access_event_id}>
        <td><strong>{event.display_name}</strong></td>
        <td><time dateTime={event.occurred_at}>{localTime(event.occurred_at)}</time></td>
        <td>{eventKindLabel(event)}</td><td>{eventLabel(event)}</td><td>{event.agent_id ?? "—"}</td>
      </tr>)}</tbody>
    </table></div>}
    {!failed && result && <nav className="access-history-pagination" aria-label="访问记录分页">
      <button type="button" disabled={result.offset === 0 || loading} onClick={() => setFilters((current) => ({ ...current, offset: Math.max(0, (current.offset ?? 0) - PAGE_SIZE) }))}>上一页</button>
      <span>第 {Math.floor(result.offset / result.limit) + 1} 页</span>
      <button type="button" disabled={!result.has_more || loading} onClick={() => setFilters((current) => ({ ...current, offset: (current.offset ?? 0) + PAGE_SIZE }))}>下一页</button>
    </nav>}
  </section>;
}
