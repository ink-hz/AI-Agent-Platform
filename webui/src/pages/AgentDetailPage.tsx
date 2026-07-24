import { useEffect, useState } from "react";

import { fetchAgent, fetchFleetOverview, fetchOperationalEvents, fetchSessions } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { OperationalEventItem } from "../components/OperationalEventItem";
import { PlatformLink } from "../components/PlatformLink";
import { SessionListItem } from "../components/SessionListItem";
import { useHistoryScrollRestoration } from "../navigationContext";
import {
  formatCount,
  formatExactLifecycleTime,
  formatLifecycleBasis,
  formatLifecycleDate,
  formatRuntimeDuration,
} from "../fleet";
import type { AgentSummary, FleetAgent, OperationalEvent, Page, SessionSummary } from "../types";


export function AgentDetailPage({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [sessions, setSessions] = useState<Page<SessionSummary> | null>(null);
  const [fleetAgent, setFleetAgent] = useState<FleetAgent | null | undefined>(undefined);
  const [error, setError] = useState(false);
  const [activity, setActivity] = useState<Page<OperationalEvent> | null>(null);
  const [activityUnavailable, setActivityUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchAgent(agentId, controller.signal),
      fetchSessions({ agent_id: agentId, limit: 50 }, controller.signal),
      fetchFleetOverview(controller.signal),
    ]).then(([nextAgent, nextSessions, fleet]) => {
      setAgent(nextAgent);
      setSessions(nextSessions);
      setFleetAgent(fleet.agents.find((item) => item.id === agentId) ?? null);
    })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [agentId]);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    setActivity(null);
    setActivityUnavailable(false);
    fetchOperationalEvents({ agent_id: agentId, limit: 8 }, controller.signal)
      .then((nextActivity) => {
        if (!disposed) setActivity(nextActivity);
      })
      .catch(() => {
        if (!disposed) setActivityUnavailable(true);
      });
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [agentId]);

  useHistoryScrollRestoration(Boolean(!error && agent && sessions && fleetAgent !== undefined));

  if (error) return <ErrorState />;
  if (!agent || agent.id !== agentId || !sessions || fleetAgent === undefined) {
    return <LoadingState label="Loading Agent profile" />;
  }
  const liveSinceBasis = fleetAgent?.live_since_basis ?? "not_recorded";
  const lastUpdatedBasis = fleetAgent?.last_updated_basis ?? "not_recorded";
  return <>
    <PlatformLink className="back-link" href="/agents">← All Agents</PlatformLink>
    <section className={`agent-profile agent-${agent.accent}`}>
      <div className="profile-identity"><span className="fleet-avatar">{agent.glyph}</span><div><p>{agent.domain}</p><h1>{agent.name}</h1><span>{agent.description}</span></div></div>
      <div className="profile-badges"><span>{agent.source_kind.toUpperCase()}</span><span>{agent.deployment}</span><span className={`freshness freshness-${agent.freshness}`}>{agent.freshness}</span></div>
      <dl className="profile-metrics"><div><dt>Sessions</dt><dd>{formatCount(agent.session_count)}</dd></div><div><dt>Conversations</dt><dd>{formatCount(agent.total_turns)}</dd></div><div><dt>Last sync</dt><dd>{agent.last_synced_at ? new Date(agent.last_synced_at).toLocaleString() : "Live source"}</dd></div></dl>
      <dl className="profile-lifecycle">
        <div>
          <dt>Live Since</dt>
          <dd>{formatLifecycleDate(fleetAgent?.live_since ?? null)}</dd>
          <small>{formatLifecycleBasis(liveSinceBasis)}</small>
        </div>
        <div>
          <dt>Last Updated</dt>
          <dd>{formatExactLifecycleTime(fleetAgent?.last_updated_at ?? null) ?? "Not recorded"}</dd>
          <small>{formatLifecycleBasis(lastUpdatedBasis)}</small>
        </div>
        <div>
          <dt>Current Runtime</dt>
          <dd>{formatRuntimeDuration(fleetAgent?.current_runtime_seconds ?? null)}</dd>
          <small>Current process only</small>
        </div>
      </dl>
    </section>
    <section className="detail-section agent-activity-section">
      <div className="section-heading">
        <div><p>OPERATIONS HISTORY</p><h2>Recent Activity</h2></div>
        <PlatformLink href={`/activity?agent_id=${encodeURIComponent(agent.id)}`}>View all activity →</PlatformLink>
      </div>
      {activityUnavailable
        ? <div className="agent-activity-status" role="alert"><strong>Activity unavailable</strong><p>Operational history could not be loaded.</p></div>
        : activity === null
          ? <p className="agent-activity-status" role="status" aria-live="polite">Loading activity</p>
          : activity.items.length === 0
            ? <p className="agent-activity-status" role="status" aria-live="polite">No operational changes recorded yet.</p>
            : <div className="operational-event-list">{activity.items.slice(0, 8).map((event) => <OperationalEventItem event={event} key={event.event_id} />)}</div>}
    </section>
    <section className="detail-section"><div className="section-heading"><div><p>CONVERSATION HISTORY</p><h2>Recent Sessions</h2></div><PlatformLink href={`/sessions?agent_id=${encodeURIComponent(agent.id)}`}>View all {sessions.total} →</PlatformLink></div>
      {sessions.items.length ? <div className="session-list">{sessions.items.map((session) => <SessionListItem key={session.session_key} session={session} />)}</div> : <EmptyState title="No Sessions yet" description="This Agent is running but has no recorded conversation history." />}
    </section>
  </>;
}
