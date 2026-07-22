import { useEffect, useMemo, useState } from "react";

import { FleetAgentCard } from "../FleetAgentCard";
import { UsageTrend } from "../UsageTrend";
import { businessAgents } from "../agentVisibility";
import { fetchFleetOverview, fetchOperationsBrief } from "../api";
import { DailyBrief } from "../components/DailyBrief";
import { UI_COPY } from "../copy";
import { runTimedPollingCycle, startPolling } from "../dashboard";
import {
  applyFleetFailure,
  applyFleetSuccess,
  formatChange,
  formatCount,
  initialFleetState,
  usageIsReadable,
} from "../fleet";
import { formatCheckedAt } from "../status";
import {
  applyOperationsFailure,
  applyOperationsSuccess,
  initialOperationsState,
} from "../operations";
import type { FleetAgent } from "../types";


function ActiveRanking({ agents }: { agents: FleetAgent[] }) {
  const leaders = useMemo(
    () => [...agents]
      .sort((a, b) =>
        (b.conversations_last_7d ?? -1) - (a.conversations_last_7d ?? -1)
        || (b.total_conversations ?? -1) - (a.total_conversations ?? -1),
      )
      .slice(0, 3),
    [agents],
  );
  const maximum = Math.max(1, ...leaders.map((agent) => agent.conversations_last_7d ?? 0));
  const hasWeeklyActivity = leaders.some((agent) => (agent.conversations_last_7d ?? 0) > 0);

  return (
    <article className="insight-card ranking-card">
      <div className="insight-heading">
        <div><p>{UI_COPY.insights.eyebrow}</p><h2>{UI_COPY.insights.ranking}</h2></div>
        <span>{UI_COPY.insights.rankingHint}</span>
      </div>
      {hasWeeklyActivity ? <ol className="ranking-list">
        {leaders.map((agent, index) => {
          const weekly = agent.conversations_last_7d ?? 0;
          return (
            <li key={agent.id}>
              <span className="ranking-index">{String(index + 1).padStart(2, "0")}</span>
              <div className={`ranking-avatar agent-${agent.accent}`}>{agent.glyph}</div>
              <div className="ranking-main">
                <div><strong>{agent.name}</strong><span>{UI_COPY.insights.conversations(formatCount(agent.conversations_last_7d))}</span></div>
                <i><b style={{ width: `${(weekly / maximum) * 100}%` }} /></i>
              </div>
            </li>
          );
        })}
      </ol> : <p className="ranking-empty">{UI_COPY.insights.emptyRanking}</p>}
    </article>
  );
}


export function OverviewPage() {
  const [state, setState] = useState(initialFleetState);
  const [operationsState, setOperationsState] = useState(initialOperationsState);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let disposed = false;
    let activeController: AbortController | null = null;
    const refresh = async () => {
      const controller = new AbortController();
      activeController = controller;
      await runTimedPollingCycle({
        controller,
        request: fetchFleetOverview,
        isDisposed: () => disposed,
        onSuccess: (overview) => setState((current) => applyFleetSuccess(current, overview)),
        onFailure: () => setState(applyFleetFailure),
        timeoutMs: 5_000,
      });
      if (activeController === controller) activeController = null;
    };
    const stopPolling = startPolling(refresh, 10_000);
    return () => { disposed = true; activeController?.abort(); stopPolling(); };
  }, []);

  useEffect(() => {
    let disposed = false;
    let activeController: AbortController | null = null;
    const refresh = async () => {
      const controller = new AbortController();
      activeController = controller;
      await runTimedPollingCycle({
        controller,
        request: fetchOperationsBrief,
        isDisposed: () => disposed,
        onSuccess: (brief) => setOperationsState((current) => applyOperationsSuccess(current, brief)),
        onFailure: () => setOperationsState(applyOperationsFailure),
        timeoutMs: 5_000,
      });
      if (activeController === controller) activeController = null;
    };
    const stopPolling = startPolling(refresh, 10_000);
    return () => { disposed = true; activeController?.abort(); stopPolling(); };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const { overview, degraded } = state;
  const visibleAgents = overview ? businessAgents(overview.agents) : [];
  const hasIncident = Boolean(overview && (overview.summary.degraded_agents > 0 || overview.summary.offline_agents > 0));
  const usageReadable = Boolean(overview && usageIsReadable(overview.usage_source));

  return <>
    <section className="hero">
      <div><p className="eyebrow">{UI_COPY.hero.eyebrow}</p><h1>{UI_COPY.hero.title}</h1><p className="hero-sub">{UI_COPY.hero.description}</p></div>
      <div className={`team-light ${hasIncident ? "incident" : "nominal"}`}>
        <span aria-hidden="true" />
        {overview ? hasIncident
          ? UI_COPY.hero.attention(overview.summary.degraded_agents + overview.summary.offline_agents)
          : UI_COPY.hero.running(overview.summary.running_agents)
          : UI_COPY.hero.loading}
      </div>
    </section>
    {degraded && <div className="banner error-banner" role="status">{UI_COPY.failures.platform}</div>}
    {overview && (!overview.usage_source.healthy || overview.usage_source.stale) && <div className="banner source-banner" role="status">{UI_COPY.failures.usage}</div>}

    {overview ? <>
      <section className="summary-section" aria-label="Fleet summary">
        <div className="section-heading"><div><p>{UI_COPY.summary.eyebrow}</p><h2>{UI_COPY.summary.title}</h2></div><span>{UI_COPY.summary.updated} {formatCheckedAt(overview.runtime_source.checked_at)}</span></div>
        <div className="fleet-summary-grid">
          <article className="fleet-summary-card"><span>{UI_COPY.summary.metrics[0]}</span><strong>{formatCount(overview.summary.total_agents)}</strong><p>{UI_COPY.summary.agentsHint}</p></article>
          <article className="fleet-summary-card summary-running"><span>{UI_COPY.summary.metrics[1]}</span><strong>{formatCount(overview.summary.running_agents)}</strong><p>{UI_COPY.summary.activeHint(overview.summary.active_agents)}</p></article>
          <article className="fleet-summary-card summary-total"><span>{UI_COPY.summary.metrics[2]}</span><strong>{formatCount(overview.summary.total_conversations)}</strong><p>{UI_COPY.summary.totalHint}</p></article>
          <article className="fleet-summary-card summary-weekly"><span>{UI_COPY.summary.metrics[3]}</span><strong>{formatCount(overview.summary.conversations_last_7d)}</strong><p>{formatChange(overview.summary.change_percent)}</p></article>
        </div>
      </section>
      {operationsState.brief && <DailyBrief brief={operationsState.brief} stale={operationsState.stale} />}
      {usageReadable ? <section className="insight-grid" aria-label="Usage insights"><UsageTrend trend={overview.trend} /><ActiveRanking agents={visibleAgents} /></section>
        : <section className="usage-unavailable-card" aria-label="Usage insights"><span aria-hidden="true">◎</span><div><h2>{UI_COPY.failures.usageTitle}</h2><p>{UI_COPY.failures.usageDescription}</p></div></section>}
      <section className="agents-section" aria-label="Agents">
        <div className="section-heading"><div><p>{UI_COPY.agent.sectionEyebrow}</p><h2>{UI_COPY.agent.sectionTitle}</h2></div><span>{UI_COPY.agent.refresh(visibleAgents.length)}</span></div>
        <div className="fleet-agent-grid">{visibleAgents.map((agent) => <FleetAgentCard agent={agent} key={agent.id} now={now} />)}</div>
      </section>
    </> : <section className="empty-state" aria-live="polite"><span className="empty-pulse" aria-hidden="true" /><h2>{degraded ? UI_COPY.loading.failedTitle : UI_COPY.loading.title}</h2><p>{degraded ? UI_COPY.loading.retry : UI_COPY.loading.description}</p></section>}
  </>;
}
