import { useEffect, useState } from "react";

import { fetchAgent, fetchAgentRuntime, fetchOperationalEvents, fetchSessions } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { channelStatusLabel, readinessLabel, sourceFreshnessLabel } from "../copy";
import { OperationalEventItem } from "../components/OperationalEventItem";
import { PlatformLink } from "../components/PlatformLink";
import { SessionListItem } from "../components/SessionListItem";
import { PLATFORM_TITLE, useDocumentTitle } from "../documentTitle";
import { useHistoryScrollRestoration } from "../navigationContext";
import type { AgentRuntimeView, AgentSummary, OperationalEvent, Page, SessionSummary } from "../types";


function productionRuntime(seconds: number | null): string {
  if (seconds === null) return "未记录运行时间";
  const days = Math.floor(seconds / 86_400);
  if (days === 0) return "今天开始运行";
  return `已运行 ${days} 天`;
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
    return <LoadingState label="正在加载 Agent 详情" />;
  }
  return <>
    <PlatformLink className="back-link" href="/agents">← 返回 Agent 列表</PlatformLink>
    <section className={`agent-profile agent-${agent.accent}`}>
      <div className="profile-identity"><span className="fleet-avatar">{agent.glyph}</span><div><p>{agent.domain}</p><h1>{agent.name}</h1><span>{agent.description}</span></div></div>
      <div className="profile-badges"><span>{agent.source_kind.toUpperCase()}</span><span>{agent.deployment}</span><span className={`freshness freshness-${agent.freshness}`}>{sourceFreshnessLabel(agent.freshness)}</span></div>
    </section>
    <section className="agent-runtime-summary" aria-labelledby="runtime-heading">
      <div className="runtime-summary-head">
        <div><h2 id="runtime-heading">运行状态</h2></div>
        {runtime && <span className={`readiness readiness-${runtime.readiness.status.toLowerCase()}`}>{readinessLabel(runtime.readiness.status)}</span>}
      </div>
      {runtimeUnavailable
        ? <p className="runtime-summary-status" role="status">运行信息暂不可用。</p>
        : runtime === null
          ? <p className="runtime-summary-status" role="status" aria-live="polite">正在加载运行状态</p>
          : <>
            <div className="runtime-primary-facts">
              <strong>{runtime.runtime.model}{runtime.runtime.backend ? ` · ${runtime.runtime.backend.toUpperCase()}` : ""}</strong>
              <span>{runtime.runtime.channel ?? "尚未观测到 Channel"}{runtime.runtime.channel ? ` ${channelStatusLabel(runtime.runtime.channel_status)}` : ""}</span>
              <span>{productionRuntime(runtime.lifecycle.production_runtime_seconds)}</span>
            </div>
            <PlatformLink href={`/agents/${encodeURIComponent(agent.id)}/runtime`}>查看运行详情 →</PlatformLink>
          </>}
    </section>
    <section className="detail-section agent-activity-section">
      <div className="section-heading">
        <div><h2>最近运行记录</h2></div>
        <PlatformLink href={`/activity?agent_id=${encodeURIComponent(agent.id)}`}>查看全部运行记录 →</PlatformLink>
      </div>
      {activityUnavailable
        ? <div className="agent-activity-status" role="alert"><strong>运行记录暂不可用</strong><p>Platform 暂时无法读取该 Agent 的运行记录。</p></div>
        : activity === null
          ? <p className="agent-activity-status" role="status" aria-live="polite">正在加载运行记录</p>
          : activity.items.length === 0
            ? <p className="agent-activity-status" role="status" aria-live="polite">暂无运行记录。</p>
            : <div className="operational-event-list">{activity.items.slice(0, 8).map((event) => <OperationalEventItem event={event} key={event.event_id} />)}</div>}
    </section>
    <section className="detail-section"><div className="section-heading"><div><h2>最近 Session</h2></div><PlatformLink href={`/sessions?agent_id=${encodeURIComponent(agent.id)}`}>查看全部 {sessions.total} 个 →</PlatformLink></div>
      {sessions.items.length ? <div className="session-list">{sessions.items.map((session) => <SessionListItem key={session.session_key} session={session} />)}</div> : <EmptyState title="暂无 Session" description="该 Agent 正在运行，但还没有采集到 Session。" />}
    </section>
  </>;
}
