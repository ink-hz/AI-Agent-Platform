import { useEffect, useState } from "react";

import { fetchAgents, fetchSessions } from "../api";
import { AgentDataSwitcher } from "../components/AgentDataSwitcher";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { SessionListItem } from "../components/SessionListItem";
import type { AgentSummary, Page, SessionSummary } from "../types";


export function FlywheelPage() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [sessions, setSessions] = useState<Page<SessionSummary> | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchAgents(controller.signal)
      .then((nextAgents) => {
        setAgents(nextAgents);
        setSelectedId((current) => current || nextAgents[0]?.id || "");
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    setSessions(null);
    setError(false);
    fetchSessions({ agent_id: selectedId, limit: 50 }, controller.signal)
      .then(setSessions)
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [selectedId]);

  if (error && agents === null) return <ErrorState />;
  if (agents === null) return <LoadingState label="Loading Agent data" />;
  if (agents.length === 0) return <EmptyState title="No Agents found" description="The Agent catalog is currently empty." />;
  const selected = agents.find((agent) => agent.id === selectedId) || agents[0];

  return <>
    <section className="page-intro"><div><p className="eyebrow">CAPTURED DATA</p><h1>Agent Data</h1><p>Select an Agent and inspect the data it has actually captured. Open any Session for the original Questions, Answers, Evidence, and Trace.</p></div></section>
    <section className="agent-data-select"><div className="section-heading"><div><p>AGENT</p><h2>Select data source</h2></div><span>{agents.length} Agents</span></div><AgentDataSwitcher agents={agents} selectedId={selected.id} onSelect={setSelectedId} /></section>
    <section className={`agent-data-context agent-${selected.accent}`}>
      <div className="profile-identity"><span className="fleet-avatar">{selected.glyph}</span><div><p>{selected.domain}</p><h2>{selected.name}</h2><span>{selected.description}</span></div></div>
      <div className="profile-badges"><span>{selected.source_kind.toUpperCase()}</span><span>{selected.deployment}</span><span className={`freshness freshness-${selected.freshness}`}>{selected.freshness}</span></div>
    </section>
    <section className="detail-section"><div className="section-heading"><div><p>CAPTURED SESSIONS</p><h2>Conversation data</h2></div>{sessions && <span>{sessions.total} Sessions</span>}</div>
      {error ? <ErrorState />
        : sessions === null ? <LoadingState label={`Loading ${selected.name} data`} />
        : sessions.items.length ? <div className="session-list">{sessions.items.map((session) => <SessionListItem key={session.session_key} session={session} showSignals={false} />)}</div>
        : <EmptyState title="No captured Sessions" description="This Agent has no recorded conversation data yet." />}
    </section>
  </>;
}
