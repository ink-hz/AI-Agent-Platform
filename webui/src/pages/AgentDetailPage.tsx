import { useEffect, useState } from "react";

import { fetchAgent, fetchAgentRuntime, fetchOperationalEvents, fetchSessions } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { OperationalEventItem } from "../components/OperationalEventItem";
import { PlatformLink } from "../components/PlatformLink";
import { SessionListItem } from "../components/SessionListItem";
import { PLATFORM_TITLE, useDocumentTitle } from "../documentTitle";
import { useHistoryScrollRestoration } from "../navigationContext";
import type { AgentRuntimeView, AgentSummary, OperationalEvent, Page, SessionSummary } from "../types";


function productionRuntime(seconds: number | null): string {
  if (seconds === null) return "Runtime not recorded";
  const days = Math.floor(seconds / 86_400);
  if (days === 0) return "Running since today";
  return `Running for ${days} day${days === 1 ? "" : "s"}`;
}


function displayChannelState(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}


export function AgentDetailPage({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [sessions, setSessions] = useState<Page<SessionSummary> | null>(null);
  const [runtime, setRuntime] = useState<AgentRuntimeView | null>(null);
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false);
  const [error, setError] = useState(false);
  const [activity, setActivity] = useState<Page<OperationalEvent> | null>(null);
  const [activityUnavailable, setActivityUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchAgent(agentId, controller.signal),
      fetchSessions({ agent_id: agentId, limit: 50 }, controller.signal),
    ]).then(([nextAgent, nextSessions]) => {
      setAgent(nextAgent);
      setSessions(nextSessions);
    })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [agentId]);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    setRuntime(null);
    setRuntimeUnavailable(false);
    fetchAgentRuntime(agentId, controller.signal)
      .then((nextRuntime) => { if (!disposed) setRuntime(nextRuntime); })
      .catch(() => { if (!disposed) setRuntimeUnavailable(true); });
    return () => {
      disposed = true;
      controller.abort();
    };
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

  useHistoryScrollRestoration(Boolean(!error && agent && sessions));
  useDocumentTitle(agent ? `${agent.name} · ${PLATFORM_TITLE}` : `Agent · ${PLATFORM_TITLE}`);

  if (error) return <ErrorState />;
  if (!agent || agent.id !== agentId || !sessions) {
    return <LoadingState label="Loading Agent profile" />;
  }
  return <>
    <PlatformLink className="back-link" href="/agents">← All Agents</PlatformLink>
    <section className={`agent-profile agent-${agent.accent}`}>
      <div className="profile-identity"><span className="fleet-avatar">{agent.glyph}</span><div><p>{agent.domain}</p><h1>{agent.name}</h1><span>{agent.description}</span></div></div>
      <div className="profile-badges"><span>{agent.source_kind.toUpperCase()}</span><span>{agent.deployment}</span><span className={`freshness freshness-${agent.freshness}`}>{agent.freshness}</span></div>
    </section>
    <section className="agent-runtime-summary" aria-labelledby="runtime-heading">
      <div className="runtime-summary-head">
        <div><p>RUNTIME</p><h2 id="runtime-heading">Runtime</h2></div>
        {runtime && <span className={`readiness readiness-${runtime.readiness.status.toLowerCase()}`}>{runtime.readiness.status}</span>}
      </div>
      {runtimeUnavailable
        ? <p className="runtime-summary-status" role="status">Runtime evidence is unavailable.</p>
        : runtime === null
          ? <p className="runtime-summary-status" role="status" aria-live="polite">Loading Runtime</p>
          : <>
            <div className="runtime-primary-facts">
              <strong>{runtime.runtime.model}{runtime.runtime.backend ? ` · ${runtime.runtime.backend.toUpperCase()}` : ""}</strong>
              <span>{runtime.runtime.channel ?? "Channel not observed"}{runtime.runtime.channel ? ` ${displayChannelState(runtime.runtime.channel_status)}` : ""}</span>
              <span>{productionRuntime(runtime.lifecycle.production_runtime_seconds)}</span>
            </div>
            <PlatformLink href={`/agents/${encodeURIComponent(agent.id)}/runtime`}>View Runtime detail →</PlatformLink>
          </>}
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
