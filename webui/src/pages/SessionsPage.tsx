import { useEffect, useState } from "react";

import { agentsForSelector } from "../agentVisibility";
import { fetchAgents, fetchSessions } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { SessionListItem } from "../components/SessionListItem";
import type { AgentSummary, Page, SessionSummary } from "../types";


export function SessionsPage() {
  const initialAgent = new URLSearchParams(window.location.search).get("agent_id") || "";
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [page, setPage] = useState<Page<SessionSummary> | null>(null);
  const [agentId, setAgentId] = useState(initialAgent);
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => { fetchAgents().then(setAgents).catch(() => undefined); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setError(false); setPage(null);
    fetchSessions({ agent_id: agentId, source_kind: source, q: search, limit: 50 }, controller.signal)
      .then(setPage).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [agentId, source, search, version]);
  const selectableAgents = agentsForSelector(agents, agentId);

  return <>
    <section className="page-intro"><div><p className="eyebrow">CONVERSATION RECORD</p><h1>Sessions</h1><p>Inspect questions, answers, Evidence, Feedback, Review, and execution Trace in one place.</p></div>{page && <strong>{page.total}<span> Sessions</span></strong>}</section>
    <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); }}>
      <label><span>Search</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Question or answer" /></label>
      <label><span>Agent</span><select value={agentId} onChange={(event) => setAgentId(event.target.value)}><option value="">All Agents</option>{selectableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <label><span>Source</span><select value={source} onChange={(event) => setSource(event.target.value)}><option value="">All sources</option><option value="metabot">MetaBot</option><option value="fae">FAE</option><option value="admin">Admin</option></select></label>
      <button type="submit">Search</button>
    </form>
    {error ? <ErrorState onRetry={() => setVersion((value) => value + 1)} />
      : page === null ? <LoadingState label="Loading Sessions" />
      : page.items.length === 0 ? <EmptyState title="No matching Sessions" description="Adjust the filters or wait for the next daily remote sync." />
      : <div className="session-list">{page.items.map((session) => <SessionListItem key={session.session_key} session={session} />)}</div>}
  </>;
}
