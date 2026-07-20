import { useEffect, useState } from "react";

import { fetchClusterStatus } from "./api";
import {
  applyFailure,
  applySuccess,
  initialDashboardState,
} from "./dashboard";
import {
  errorLabel,
  formatCheckedAt,
  formatLatency,
  formatUptime,
  isStale,
  statusMeta,
} from "./status";
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

  useEffect(() => {
    let stopped = false;
    const refresh = async () => {
      try {
        const snapshot = await fetchClusterStatus();
        if (!stopped) {
          setState((current) => applySuccess(current, snapshot));
        }
      } catch {
        if (!stopped) setState(applyFailure);
      }
    };

    refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
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
                {snapshot.instances.map((instance) => {
                  const meta = statusMeta(instance.status);
                  const stale = isStale(instance.checked_at);
                  const probeError = errorLabel(instance.error);
                  return (
                    <article
                      className={`instance-card ${meta.tone}${stale ? " stale" : ""}`}
                      key={instance.id}
                    >
                      <div className="instance-head">
                        <div>
                          <h3>{instance.name}</h3>
                          <p>{instance.pm2_name}</p>
                        </div>
                        <span className={`status-badge ${meta.tone}`}>
                          <i aria-hidden="true" />
                          {meta.label}
                        </span>
                      </div>

                      <dl className="instance-metrics">
                        <div>
                          <dt>API 端口</dt>
                          <dd>{instance.port}</dd>
                        </div>
                        <div>
                          <dt>运行时长</dt>
                          <dd>{formatUptime(instance.uptime_seconds)}</dd>
                        </div>
                        <div>
                          <dt>响应延迟</dt>
                          <dd>{formatLatency(instance.latency_ms)}</dd>
                        </div>
                        <div>
                          <dt>最后检测</dt>
                          <dd>{formatCheckedAt(instance.checked_at)}</dd>
                        </div>
                      </dl>

                      <div className="instance-foot">
                        {stale ? (
                          <span className="stale-label">数据已过期</span>
                        ) : (
                          <span className="fresh-label">数据新鲜</span>
                        )}
                        {probeError && <span className="probe-error">{probeError}</span>}
                      </div>
                    </article>
                  );
                })}
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
