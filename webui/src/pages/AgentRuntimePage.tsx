import { useEffect, useState } from "react";

import { fetchAgent, fetchAgentRuntime } from "../api";
import { ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { PLATFORM_TITLE, useDocumentTitle } from "../documentTitle";
import { formatExactLifecycleTime, formatLifecycleDate } from "../fleet";
import type { AgentRuntimeView, AgentSummary } from "../types";


function compactDuration(seconds: number | null): string {
  if (seconds === null) return "Not observed";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes || parts.length === 0) parts.push(`${minutes}m`);
  return parts.slice(0, 2).join(" ");
}


function productionRuntime(seconds: number | null): string {
  if (seconds === null) return "Runtime not recorded";
  const days = Math.floor(seconds / 86_400);
  if (days === 0) return "Running since today";
  return `Running for ${days} day${days === 1 ? "" : "s"}`;
}


function modelEvidenceLabel(source: AgentRuntimeView["runtime"]["model_source"]): string {
  switch (source) {
    case "runtime": return "Observed active model";
    case "trace": return "Latest completed Trace";
    case "configured": return "Configured model · not observed in a run";
    default: return "No model evidence";
  }
}


export function AgentRuntimePage({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [runtime, setRuntime] = useState<AgentRuntimeView | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setAgent(null);
    setRuntime(null);
    setError(false);
    Promise.all([
      fetchAgent(agentId, controller.signal),
      fetchAgentRuntime(agentId, controller.signal),
    ]).then(([nextAgent, nextRuntime]) => {
      setAgent(nextAgent);
      setRuntime(nextRuntime);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [agentId]);

  useDocumentTitle(
    agent
      ? `Runtime · ${agent.name} · ${PLATFORM_TITLE}`
      : `Runtime · ${PLATFORM_TITLE}`,
  );

  if (error) return <ErrorState />;
  if (!agent || !runtime) return <LoadingState label="Loading Runtime evidence" />;

  return <>
    <PlatformLink className="back-link" href={`/agents/${encodeURIComponent(agent.id)}`}>← {agent.name}</PlatformLink>
    <header className="runtime-detail-head">
      <div>
        <p>RUNTIME DETAIL</p>
        <h1>{agent.name} Runtime</h1>
        <span>{runtime.readiness.reason}</span>
      </div>
      <strong className={`readiness readiness-${runtime.readiness.status.toLowerCase()}`}>{runtime.readiness.status}</strong>
    </header>

    <section className="runtime-detail-grid" aria-label="Runtime facts">
      <article className="runtime-fact runtime-fact-primary">
        <span>Model</span>
        <strong>{runtime.runtime.model}</strong>
        <small>{modelEvidenceLabel(runtime.runtime.model_source)}</small>
      </article>
      <article className="runtime-fact">
        <span>Engine · Backend</span>
        <strong>{[runtime.runtime.engine, runtime.runtime.backend?.toUpperCase()].filter(Boolean).join(" · ") || "Not observed"}</strong>
        <small>Execution runtime</small>
      </article>
      <article className="runtime-fact">
        <span>Primary channel</span>
        <strong>{runtime.runtime.channel ?? "Not observed"}</strong>
        <small>{runtime.runtime.channel_status}</small>
      </article>
      <article className="runtime-fact">
        <span>Current process</span>
        <strong>{compactDuration(runtime.runtime.process_uptime_seconds)}</strong>
        <small>Resets when the service restarts</small>
      </article>
    </section>

    <section className="runtime-lifecycle detail-section">
      <div className="section-heading"><div><p>LIFECYCLE</p><h2>Production timeline</h2></div></div>
      <dl>
        <div><dt>Production runtime</dt><dd>{productionRuntime(runtime.lifecycle.production_runtime_seconds)}</dd></div>
        <div><dt>Live Since</dt><dd>{formatLifecycleDate(runtime.lifecycle.live_since)}</dd></div>
        <div><dt>Last Updated</dt><dd>{formatExactLifecycleTime(runtime.lifecycle.last_updated_at) ?? "Not recorded"}</dd></div>
      </dl>
    </section>

    <section className="runtime-evidence detail-section">
      <div className="section-heading"><div><p>EVIDENCE</p><h2>Observed sources</h2></div></div>
      {runtime.evidence.length
        ? <div className="runtime-evidence-list">{runtime.evidence.map((item, index) => <article key={`${item.kind}-${item.source}-${index}`}>
          <div><span>{item.kind}</span><b>{item.status}</b></div>
          <strong>{item.source}</strong>
          <p>{item.summary}</p>
          {item.observed_at && <time>{new Date(item.observed_at).toLocaleString()}</time>}
        </article>)}</div>
        : <p className="runtime-empty-evidence">No detailed runtime evidence has been observed yet.</p>}
    </section>
  </>;
}
