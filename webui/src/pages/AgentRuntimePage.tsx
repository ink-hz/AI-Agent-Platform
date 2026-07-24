import { useEffect, useState } from "react";

import { fetchAgent, fetchAgentRuntime } from "../api";
import { ErrorState, LoadingState } from "../components/DataState";
import {
  channelStatusLabel,
  readinessLabel,
  readinessReasonLabel,
} from "../copy";
import { PlatformLink } from "../components/PlatformLink";
import { PLATFORM_TITLE, useDocumentTitle } from "../documentTitle";
import { formatExactLifecycleTime, formatLifecycleDate } from "../fleet";
import type { AgentRuntimeView, AgentSummary } from "../types";


function compactDuration(seconds: number | null): string {
  if (seconds === null) return "尚未观测";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days}天`);
  if (hours) parts.push(`${hours}小时`);
  if (minutes || parts.length === 0) parts.push(`${minutes}分钟`);
  return parts.slice(0, 2).join(" ");
}


function productionRuntime(seconds: number | null): string {
  if (seconds === null) return "未记录运行时间";
  const days = Math.floor(seconds / 86_400);
  if (days === 0) return "今天开始运行";
  return `已运行 ${days} 天`;
}


function modelEvidenceLabel(source: AgentRuntimeView["runtime"]["model_source"]): string {
  switch (source) {
    case "runtime": return "运行时观测";
    case "trace": return "最近完成的 Trace";
    case "configured": return "配置模型 · 尚未在运行中观测";
    default: return "暂无模型观测";
  }
}


function modelLabel(runtime: AgentRuntimeView): string {
  return runtime.runtime.model_source === "unavailable"
    ? "尚未观测到 Model"
    : runtime.runtime.model;
}


function evidenceKind(value: string): string {
  if (value === "process") return "进程";
  if (value === "runtime") return "运行时";
  if (value === "trace") return "Trace";
  return value;
}


function evidenceSummary(source: string): string {
  if (source === "health_probe") return "进程健康检查";
  if (source === "runtime_observation") return "实时运行观测";
  if (source === "latest_completed_trace") return "最近完成的 Trace";
  return "运行观测记录";
}


function evidenceStatus(value: string): string {
  if (value === "current") return "实时";
  if (value === "healthy") return "正常";
  if (value === "available") return "可用";
  return value;
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
      ? `运行详情 · ${agent.name} · ${PLATFORM_TITLE}`
      : `运行详情 · ${PLATFORM_TITLE}`,
  );

  if (error) return <ErrorState />;
  if (!agent || !runtime) return <LoadingState label="正在加载运行详情" />;

  return <>
    <PlatformLink className="back-link" href={`/agents/${encodeURIComponent(agent.id)}`}>← 返回 {agent.name}</PlatformLink>
    <header className="runtime-detail-head">
      <div>
        <h1>{agent.name} 运行详情</h1>
        <span>{readinessReasonLabel(runtime.readiness.status)}</span>
      </div>
      <strong className={`readiness readiness-${runtime.readiness.status.toLowerCase()}`}>{readinessLabel(runtime.readiness.status)}</strong>
    </header>

    <section className="detail-section runtime-environment">
      <div className="section-heading"><div><h2>当前运行状态</h2></div><span>运行环境</span></div>
    <section className="runtime-detail-grid" aria-label="Runtime facts">
      <article className="runtime-fact runtime-fact-primary">
        <span>Model</span>
        <strong>{modelLabel(runtime)}</strong>
        <small>{modelEvidenceLabel(runtime.runtime.model_source)}</small>
      </article>
      <article className="runtime-fact">
        <span>Engine · Backend</span>
        <strong>{[runtime.runtime.engine, runtime.runtime.backend?.toUpperCase()].filter(Boolean).join(" · ") || "尚未观测"}</strong>
        <small>执行环境</small>
      </article>
      <article className="runtime-fact">
        <span>Channel</span>
        <strong>{runtime.runtime.channel ?? "尚未观测"}</strong>
        <small>{channelStatusLabel(runtime.runtime.channel_status)}</small>
      </article>
      <article className="runtime-fact">
        <span>当前进程</span>
        <strong>{compactDuration(runtime.runtime.process_uptime_seconds)}</strong>
        <small>进程重启后重新计时</small>
      </article>
    </section>
    </section>

    <section className="runtime-lifecycle detail-section">
      <div className="section-heading"><div><h2>运行周期</h2></div></div>
      <dl>
        <div><dt>生产运行时间</dt><dd>{productionRuntime(runtime.lifecycle.production_runtime_seconds)}</dd></div>
        <div><dt>上线时间</dt><dd>{formatLifecycleDate(runtime.lifecycle.live_since)}</dd></div>
        <div><dt>最近更新</dt><dd>{formatExactLifecycleTime(runtime.lifecycle.last_updated_at) ?? "未记录"}</dd></div>
      </dl>
    </section>

    <section className="runtime-evidence detail-section">
      <div className="section-heading"><div><h2>观测依据</h2></div></div>
      {runtime.evidence.length
        ? <div className="runtime-evidence-list">{runtime.evidence.map((item, index) => <article key={`${item.kind}-${item.source}-${index}`}>
          <div><span>{evidenceKind(item.kind)}</span><b>{evidenceStatus(item.status)}</b></div>
          <strong>{item.source}</strong>
          <p>{evidenceSummary(item.source)}</p>
          {item.observed_at && <time>{new Date(item.observed_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</time>}
        </article>)}</div>
        : <p className="runtime-empty-evidence">暂未观测到详细运行依据。</p>}
    </section>
  </>;
}
