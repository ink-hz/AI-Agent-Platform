import { useEffect, useState } from "react";

import { agentsForSelector } from "../agentVisibility";
import { fetchAgents, fetchSessions } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { SessionListItem } from "../components/SessionListItem";
import { useHistoryScrollRestoration } from "../navigationContext";
import { currentLocationPath, navigate } from "../router";
import {
  sessionFiltersFromSearch,
  sessionsPath,
  type SessionFilters,
  type SessionSource,
} from "../sessionNavigation";
import type { AgentSummary, Page, SessionSummary } from "../types";


const PAGE_SIZE = 50;


export function SessionsPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [page, setPage] = useState<Page<SessionSummary> | null>(null);
  const [draft, setDraft] = useState<SessionFilters>(() => sessionFiltersFromSearch(window.location.search));
  const [applied, setApplied] = useState<SessionFilters>(() => sessionFiltersFromSearch(window.location.search));
  const [error, setError] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => { fetchAgents().then(setAgents).catch(() => undefined); }, []);
  useEffect(() => {
    const restore = () => {
      if (window.location.pathname !== "/sessions") return;
      const next = sessionFiltersFromSearch(window.location.search);
      const canonical = sessionsPath(next);
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
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setError(false); setPage(null);
    const { page: requestedPage, ...filters } = applied;
    const offset = (requestedPage - 1) * PAGE_SIZE;
    fetchSessions({
      ...filters,
      limit: PAGE_SIZE,
      ...(offset > 0 ? { offset } : {}),
    }, controller.signal)
      .then((nextPage) => {
        if (controller.signal.aborted) return;
        const lastPage = Math.max(1, Math.ceil(nextPage.total / PAGE_SIZE));
        if (requestedPage > lastPage) {
          navigate(sessionsPath({ ...applied, page: lastPage }), { replace: true });
          return;
        }
        setPage(nextPage);
      }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [applied, version]);
  const selectableAgents = agentsForSelector(agents, draft.agent_id);
  useHistoryScrollRestoration(page !== null && !error);

  const apply = (next: SessionFilters) => {
    const path = sessionsPath(next);
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
    navigate(sessionsPath({ ...applied, page: selected }));
  };

  return <>
    <section className="page-intro"><div><h1>Session</h1><p>查看各 Agent 的真实 Session 和对话记录。</p></div>{page && <strong>{page.total}<span> 个 Session</span></strong>}</section>
    <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); apply({ ...applied, q: draft.q.trim(), page: 1 }); }}>
      <label><span>搜索</span><input name="q" value={draft.q} onChange={(event) => setDraft((current) => ({ ...current, q: event.target.value }))} placeholder="用户提问或 Agent 回答" /></label>
      <label><span>Agent</span><select name="agent_id" value={draft.agent_id} onChange={(event) => { const agent_id = event.target.value; setDraft((current) => ({ ...current, agent_id, page: 1 })); apply({ ...applied, agent_id, page: 1 }); }}><option value="">全部业务 Agent</option>{selectableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <label><span>来源</span><select name="source_kind" value={draft.source_kind} onChange={(event) => { const source_kind = event.target.value as SessionSource; setDraft((current) => ({ ...current, source_kind, page: 1 })); apply({ ...applied, source_kind, page: 1 }); }}><option value="">全部来源</option><option value="metabot">MetaBot</option><option value="fae">FAE</option><option value="admin">Admin</option></select></label>
      <button type="submit">搜索</button>
    </form>
    {error ? <ErrorState onRetry={() => setVersion((value) => value + 1)} />
      : page === null ? <LoadingState label="正在加载 Session" />
      : page.items.length === 0 ? <EmptyState title="没有符合条件的 Session" description="请调整筛选条件，或等待下一次数据同步。" />
      : <div className="session-list">{page.items.map((session) => <SessionListItem key={session.session_key} session={session} />)}</div>}
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
