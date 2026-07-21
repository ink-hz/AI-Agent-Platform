import { useEffect, useState } from "react";

import { AgentCard } from "./AgentCard";
import { fetchClusterStatus } from "./api";
import {
  applyFailure,
  applySuccess,
  initialDashboardState,
  startPolling,
} from "./dashboard";
import { formatCheckedAt } from "./status";
import type { ClusterSummary } from "./types";


const SUMMARY_ITEMS: Array<{
  key: keyof ClusterSummary;
  label: string;
  tone: string;
}> = [
  { key: "total", label: "实例总数", tone: "neutral" },
  { key: "healthy", label: "健康", tone: "healthy" },
  { key: "degraded", label: "异常", tone: "degraded" },
  { key: "offline", label: "离线", tone: "offline" },
  { key: "checking", label: "检测中", tone: "checking" },
];


export default function App() {
  const [state, setState] = useState(initialDashboardState);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let disposed = false;
    let activeController: AbortController | null = null;
    const refresh = async () => {
      const controller = new AbortController();
      activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 5_000);
      try {
        const snapshot = await fetchClusterStatus(controller.signal);
        if (!disposed) {
          setState((current) => applySuccess(current, snapshot));
        }
      } catch {
        if (!disposed) setState(applyFailure);
      } finally {
        window.clearTimeout(timeout);
        if (activeController === controller) activeController = null;
      }
    };

    const stopPolling = startPolling(refresh, 10_000);
    return () => {
      disposed = true;
      activeController?.abort();
      stopPolling();
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 5_000);
    return () => window.clearInterval(timer);
  }, []);

  const { snapshot, degraded } = state;
  const hasIncident = Boolean(
    snapshot && (snapshot.summary.degraded > 0 || snapshot.summary.offline > 0),
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <img
              className="brand-mark"
              src="/platform-logo.svg"
              alt=""
              aria-hidden="true"
            />
            <span className="brand-name">
              <strong>Orbbec</strong> MetaBot Cluster Monitor
            </span>
          </div>
          <span className="topbar-tag">只读监控 · 10 秒刷新</span>
        </div>
      </header>

      <main className="page">
        <section className="hero">
          <div>
            <p className="eyebrow">AGENT OPERATIONS</p>
            <h1>MetaBot 集群状态</h1>
            <p className="hero-sub">
              实时观察本机 Agent Bot 实例的存活状态、运行时长与响应延迟。
            </p>
          </div>
          <div className={`cluster-light ${hasIncident ? "incident" : "nominal"}`}>
            <span className="cluster-light-dot" aria-hidden="true" />
            {snapshot ? (hasIncident ? "集群存在异常" : "集群运行正常") : "正在读取状态"}
          </div>
        </section>

        {degraded && (
          <div className="banner error-banner" role="status">
            监控接口暂不可用，正在显示最后一次成功状态。
          </div>
        )}
        {snapshot && !snapshot.source.healthy && (
          <div className="banner source-banner" role="status">
            运行契约读取异常，实例列表来自最后一次有效快照。
          </div>
        )}

        <section className="monitor-intro" aria-labelledby="monitor-intro-title">
          <div className="intro-copy">
            <span className="intro-mark" aria-hidden="true">◎</span>
            <div>
              <p className="intro-label">看板说明</p>
              <h2 id="monitor-intro-title">专注观察每一个 Agent Bot</h2>
              <p>
                看板从本机运行契约自动发现 MetaBot 实例，持续呈现它们的存活状态、运行时长和响应速度。
              </p>
            </div>
          </div>
          <div className="intro-facts" aria-label="监控规则">
            <span><i aria-hidden="true" />10 秒自动刷新</span>
            <span><i aria-hidden="true" />健康接口探测</span>
            <span><i aria-hidden="true" />只读，不控制 Agent</span>
          </div>
        </section>

        {snapshot ? (
          <>
            <section className="summary-section" aria-label="集群摘要">
              <div className="section-heading">
                <h2>集群概览</h2>
                <span>
                  最后刷新 {formatCheckedAt(snapshot.source.checked_at)}
                </span>
              </div>
              <div className="summary-grid">
                {SUMMARY_ITEMS.map((item) => (
                  <article className={`summary-card ${item.tone}`} key={item.key}>
                    <span>{item.label}</span>
                    <strong>{snapshot.summary[item.key]}</strong>
                  </article>
                ))}
              </div>
            </section>

            <section className="instances-section" aria-label="MetaBot 实例">
              <div className="section-heading">
                <h2>实例状态</h2>
                <span>{snapshot.instances.length} 个监控目标</span>
              </div>
              <div className="instance-grid">
                {snapshot.instances.map((instance) => (
                  <AgentCard instance={instance} key={instance.id} now={now} />
                ))}
              </div>
            </section>
          </>
        ) : (
          <section className="empty-state" aria-live="polite">
            <span className="empty-pulse" aria-hidden="true" />
            <h2>{degraded ? "无法连接监控服务" : "正在加载集群状态"}</h2>
            <p>{degraded ? "Platform 会继续自动重试。" : "首次探测最多需要几秒钟。"}</p>
          </section>
        )}
      </main>

      <footer className="site-foot">
        <span>Orbbec MetaBot Cluster Monitor</span>
        <span className="dot">·</span>
        <span>只读探测，不控制 Agent</span>
      </footer>
    </div>
  );
}
