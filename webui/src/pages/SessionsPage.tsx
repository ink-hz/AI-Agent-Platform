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
    fetchSessions({ ...applied, limit: 50 }, controller.signal)
      .then(setPage).catch(() => { if (!controller.signal.aborted) setError(true); });
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

  return <>
    <section className="page-intro"><div><h1>Session</h1><p>查看各 Agent 的真实 Session 和对话记录。</p></div>{page && <strong>{page.total}<span> 个 Session</span></strong>}</section>
    <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); apply({ ...applied, q: draft.q.trim() }); }}>
      <label><span>搜索</span><input name="q" value={draft.q} onChange={(event) => setDraft((current) => ({ ...current, q: event.target.value }))} placeholder="用户提问或 Agent 回答" /></label>
      <label><span>Agent</span><select name="agent_id" value={draft.agent_id} onChange={(event) => { const agent_id = event.target.value; setDraft((current) => ({ ...current, agent_id })); apply({ ...applied, agent_id }); }}><option value="">全部业务 Agent</option>{selectableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <label><span>来源</span><select name="source_kind" value={draft.source_kind} onChange={(event) => { const source_kind = event.target.value as SessionSource; setDraft((current) => ({ ...current, source_kind })); apply({ ...applied, source_kind }); }}><option value="">全部来源</option><option value="metabot">MetaBot</option><option value="fae">FAE</option><option value="admin">Admin</option></select></label>
      <button type="submit">搜索</button>
    </form>
    {error ? <ErrorState onRetry={() => setVersion((value) => value + 1)} />
      : page === null ? <LoadingState label="正在加载 Session" />
      : page.items.length === 0 ? <EmptyState title="没有符合条件的 Session" description="请调整筛选条件，或等待下一次数据同步。" />
      : <div className="session-list">{page.items.map((session) => <SessionListItem key={session.session_key} session={session} />)}</div>}
  </>;
}
