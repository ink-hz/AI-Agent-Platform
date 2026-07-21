import { useEffect, useState } from "react";

import { fetchAgent, fetchSessions } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { SessionListItem } from "../components/SessionListItem";
import { formatCount } from "../fleet";
import type { AgentSummary, Page, SessionSummary } from "../types";


export function AgentDetailPage({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [sessions, setSessions] = useState<Page<SessionSummary> | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchAgent(agentId, controller.signal),
      fetchSessions({ agent_id: agentId, limit: 50 }, controller.signal),
    ]).then(([nextAgent, nextSessions]) => { setAgent(nextAgent); setSessions(nextSessions); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [agentId]);

  if (error) return <ErrorState />;
  if (!agent || !sessions) return <LoadingState label="Loading Agent profile" />;
  return <>
    <PlatformLink className="back-link" href="/agents">← All Agents</PlatformLink>
    <section className={`agent-profile agent-${agent.accent}`}>
      <div className="profile-identity"><span className="fleet-avatar">{agent.glyph}</span><div><p>{agent.domain}</p><h1>{agent.name}</h1><span>{agent.description}</span></div></div>
      <div className="profile-badges"><span>{agent.source_kind.toUpperCase()}</span><span>{agent.deployment}</span><span className={`freshness freshness-${agent.freshness}`}>{agent.freshness}</span></div>
      <dl className="profile-metrics"><div><dt>Sessions</dt><dd>{formatCount(agent.session_count)}</dd></div><div><dt>Conversations</dt><dd>{formatCount(agent.total_turns)}</dd></div><div><dt>Last sync</dt><dd>{agent.last_synced_at ? new Date(agent.last_synced_at).toLocaleString() : "Live source"}</dd></div></dl>
    </section>
    <section className="detail-section"><div className="section-heading"><div><p>CONVERSATION HISTORY</p><h2>Recent Sessions</h2></div><PlatformLink href={`/sessions?agent_id=${encodeURIComponent(agent.id)}`}>View all {sessions.total} →</PlatformLink></div>
      {sessions.items.length ? <div className="session-list">{sessions.items.map((session) => <SessionListItem key={session.session_key} session={session} />)}</div> : <EmptyState title="No Sessions yet" description="This Agent is running but has no recorded conversation history." />}
    </section>
  </>;
}
