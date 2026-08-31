import { useEffect, useState } from "react";

import { agentsForSelector } from "../agentVisibility";
import { fetchAgents, fetchSessions, type SessionQuery } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { SessionListItem } from "../components/SessionListItem";
import { useHistoryScrollRestoration } from "../navigationContext";
import { currentLocationPath, navigate } from "../router";
import {
  sessionFiltersFromSearch,
  normalizeFaeSessionDate,
  sessionsPath,
  type SessionFilters,
  type SessionSource,
} from "../sessionNavigation";
import type { AgentSummary, Page, SessionSummary } from "../types";


const PAGE_SIZE = 50;


export interface SessionsViewProps {
  basePath: string;
  title: string;
  description: string;
  fixedScope?: Pick<SessionQuery, "agent_id" | "source_kind">;
  showScopeFilters: boolean;
  load: (query: SessionQuery & { date_before?: string }, signal: AbortSignal) => Promise<Page<SessionSummary>>;
  detailHref: (session: SessionSummary) => string;
}


export function SessionsView({
  basePath,
  title,
  description,
  fixedScope,
  showScopeFilters,
  load,
  detailHref,
}: SessionsViewProps) {
  const filtersFromSearch = () => {
    const filters = sessionFiltersFromSearch(window.location.search);
    return showScopeFilters
      ? { ...filters, channel: "", sentiment: "" as SessionFilters["sentiment"], review_status: "", outcome: "", date_from: "", date_to: "", date_before: "" }
      : { ...filters, agent_id: "", source_kind: "" as SessionSource };
  };
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [page, setPage] = useState<Page<SessionSummary> | null>(null);
  const [draft, setDraft] = useState<SessionFilters>(filtersFromSearch);
  const [applied, setApplied] = useState<SessionFilters>(filtersFromSearch);
  const [error, setError] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!showScopeFilters) {
      setAgents([]);
      return;
    }
    fetchAgents().then(setAgents).catch(() => undefined);
  }, [showScopeFilters]);
  useEffect(() => {
    const restore = () => {
      if (window.location.pathname !== basePath) return;
      const next = filtersFromSearch();
      const canonical = sessionsPath(next, basePath);
      if (currentLocationPath() !== canonical) {
        navigate(canonical, { replace: true });
        return;
      }
      setDraft(next);
      setApplied(next);
    };
    restore();
    window.addEventListener("popstate", restore);
    window.addEventListener("platform:navigate", restore);
    return () => {
      window.removeEventListener("popstate", restore);
      window.removeEventListener("platform:navigate", restore);
    };
  }, [basePath]);
  useEffect(() => {
    const controller = new AbortController();
    setError(false); setPage(null);
    const { page: requestedPage, ...filters } = applied;
    const offset = (requestedPage - 1) * PAGE_SIZE;
    load({
      ...filters,
      ...fixedScope,
      limit: PAGE_SIZE,
      ...(offset > 0 ? { offset } : {}),
    }, controller.signal)
      .then((nextPage) => {
        if (controller.signal.aborted) return;
        const lastPage = Math.max(1, Math.ceil(nextPage.total / PAGE_SIZE));
        if (requestedPage > lastPage) {
          navigate(sessionsPath({ ...applied, page: lastPage }, basePath), { replace: true });
          return;
        }
        setPage(nextPage);
      }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [applied, basePath, fixedScope, load, version]);
  const selectableAgents = agentsForSelector(agents, draft.agent_id);
  useHistoryScrollRestoration(page !== null && !error);

  const apply = (next: SessionFilters) => {
    const path = sessionsPath(next, basePath);
    if (currentLocationPath() === path) {
      setDraft(next);
      setApplied(next);
    } else {
      navigate(path, { replace: true });
    }
  };
  const totalPages = page ? Math.max(1, Math.ceil(page.total / PAGE_SIZE)) : 1;
  const visibleStart = page && page.total > 0 ? page.offset + 1 : 0;
  const visibleEnd = page ? page.offset + page.items.length : 0;
  const goToPage = (target: number) => {
    const selected = Math.min(totalPages, Math.max(1, target));
    if (selected === applied.page) return;
    navigate(sessionsPath({ ...applied, page: selected }, basePath));
  };
  const submit = () => {
    if (showScopeFilters) {
      apply({ ...applied, q: draft.q.trim(), page: 1 });
      return;
    }
    apply({
      ...draft,
      q: draft.q.trim(),
      channel: draft.channel.trim(),
      review_status: draft.review_status.trim(),
      outcome: draft.outcome.trim(),
      date_from: normalizeFaeSessionDate(draft.date_from),
      date_to: normalizeFaeSessionDate(draft.date_to),
      date_before: normalizeFaeSessionDate(draft.date_before),
      page: 1,
    });
  };

  return <>
    <section className="page-intro"><div><h1>{title}</h1><p>{description}</p></div>{page && <strong>{page.total}<span> 个 Session</span></strong>}</section>
    <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <label><span>搜索</span><input name="q" value={draft.q} onChange={(event) => setDraft((current) => ({ ...current, q: event.target.value }))} placeholder="用户提问或 Agent 回答" /></label>
      {showScopeFilters && <>
        <label><span>Agent</span><select name="agent_id" value={draft.agent_id} onChange={(event) => { const agent_id = event.target.value; setDraft((current) => ({ ...current, agent_id, page: 1 })); apply({ ...applied, agent_id, page: 1 }); }}><option value="">全部业务 Agent</option>{selectableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
        <label><span>来源</span><select name="source_kind" value={draft.source_kind} onChange={(event) => { const source_kind = event.target.value as SessionSource; setDraft((current) => ({ ...current, source_kind, page: 1 })); apply({ ...applied, source_kind, page: 1 }); }}><option value="">全部来源</option><option value="metabot">MetaBot</option><option value="fae">FAE</option><option value="admin">Admin</option></select></label>
      </>}
      {!showScopeFilters && <>
        <label><span>Channel</span><input name="channel" value={draft.channel} onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))} /></label>
        <label><span>反馈情感</span><select name="sentiment" value={draft.sentiment} onChange={(event) => setDraft((current) => ({ ...current, sentiment: event.target.value as SessionFilters["sentiment"] }))}><option value="">全部</option><option value="positive">正向</option><option value="negative">负向</option><option value="other">其他</option></select></label>
        <label><span>复审状态</span><input name="review_status" value={draft.review_status} onChange={(event) => setDraft((current) => ({ ...current, review_status: event.target.value }))} /></label>
        <label><span>结果</span><input name="outcome" value={draft.outcome} onChange={(event) => setDraft((current) => ({ ...current, outcome: event.target.value }))} /></label>
        <label><span>开始时间</span><input name="date_from" value={draft.date_from} onChange={(event) => setDraft((current) => ({ ...current, date_from: event.target.value }))} placeholder="2026-08-01T00:00:00+08:00" /></label>
        <label><span>结束时间</span><input name="date_to" value={draft.date_to} onChange={(event) => setDraft((current) => ({ ...current, date_to: event.target.value }))} placeholder="2026-08-31T23:59:59+08:00" /></label>
        <label><span>截止前（不含）</span><input name="date_before" value={draft.date_before} onChange={(event) => setDraft((current) => ({ ...current, date_before: event.target.value }))} placeholder="2026-09-01T00:00:00+08:00" /></label>
      </>}
      <button type="submit">搜索</button>
    </form>
    {error ? <ErrorState onRetry={() => setVersion((value) => value + 1)} />
      : page === null ? <LoadingState label="正在加载 Session" />
      : page.items.length === 0 ? <EmptyState title="没有符合条件的 Session" description="请调整筛选条件，或等待下一次数据同步。" />
      : <div className="session-list">{page.items.map((session) => <SessionListItem key={session.session_key} session={session} detailHref={detailHref(session)} />)}</div>}
    {page && page.items.length > 0 && <nav className="session-pagination" aria-label="Session 分页">
      <p>第 {visibleStart}–{visibleEnd} 条，共 {page.total} 条</p>
      <div>
        <button type="button" disabled={applied.page === 1} onClick={() => goToPage(1)}>首页</button>
        <button type="button" disabled={applied.page === 1} onClick={() => goToPage(applied.page - 1)}>上一页</button>
        <span>第 {applied.page} / {totalPages} 页</span>
        <button type="button" disabled={applied.page === totalPages} onClick={() => goToPage(applied.page + 1)}>下一页</button>
        <button type="button" disabled={applied.page === totalPages} onClick={() => goToPage(totalPages)}>末页</button>
      </div>
    </nav>}
  </>;
}


const genericDetailHref = (session: SessionSummary) => `/admin/sessions/${encodeURIComponent(session.session_key)}`;


export function SessionsPage() {
  return <SessionsView
    basePath="/admin/sessions"
    title="Session"
    description="查看各 Agent 的真实 Session 和对话记录。"
    showScopeFilters
    load={fetchSessions}
    detailHref={genericDetailHref}
  />;
}
