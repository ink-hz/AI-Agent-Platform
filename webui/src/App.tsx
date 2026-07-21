import { useEffect, useMemo, useState } from "react";

import { FleetAgentCard } from "./FleetAgentCard";
import { UsageTrend } from "./UsageTrend";
import { fetchFleetOverview } from "./api";
import { startPolling } from "./dashboard";
import {
  applyFleetFailure,
  applyFleetSuccess,
  formatChange,
  formatCount,
  initialFleetState,
  usageIsReadable,
} from "./fleet";
import { formatCheckedAt } from "./status";
import type { FleetAgent } from "./types";


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
        <div>
          <p>本周表现</p>
          <h2>活跃 Agent</h2>
        </div>
        <span>按真实对话排序</span>
      </div>
      {hasWeeklyActivity ? <ol className="ranking-list">
        {leaders.map((agent, index) => {
          const weekly = agent.conversations_last_7d ?? 0;
          return (
            <li key={agent.id}>
              <span className="ranking-index">{String(index + 1).padStart(2, "0")}</span>
              <div className={`ranking-avatar agent-${agent.accent}`}>{agent.glyph}</div>
              <div className="ranking-main">
                <div><strong>{agent.name}</strong><span>{formatCount(agent.conversations_last_7d)} 次</span></div>
                <i><b style={{ width: `${(weekly / maximum) * 100}%` }} /></i>
              </div>
            </li>
          );
        })}
      </ol> : <p className="ranking-empty">近 7 天还没有真实对话</p>}
    </article>
  );
}


export default function App() {
  const [state, setState] = useState(initialFleetState);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let disposed = false;
    let activeController: AbortController | null = null;
    const refresh = async () => {
      const controller = new AbortController();
      activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 5_000);
      try {
        const overview = await fetchFleetOverview(controller.signal);
        if (!disposed) setState((current) => applyFleetSuccess(current, overview));
      } catch {
        if (!disposed) setState(applyFleetFailure);
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
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const { overview, degraded } = state;
  const hasIncident = Boolean(
    overview && (overview.summary.degraded_agents > 0 || overview.summary.offline_agents > 0),
  );
  const usageReadable = Boolean(overview && usageIsReadable(overview.usage_source));

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <img className="brand-mark" src="/platform-logo.svg" alt="" aria-hidden="true" />
            <span className="brand-name"><strong>Orbbec</strong> Agent Platform</span>
          </div>
          <nav className="product-nav" aria-label="产品导航">
            <span className="is-current">总览</span>
            <span>Agents</span>
            <span>任务与会话</span>
            <span>数据飞轮</span>
          </nav>
          <span className="readonly-tag">只读观察</span>
        </div>
      </header>

      <main className="page">
        <section className="hero">
          <div>
            <p className="eyebrow">AI TEAM COCKPIT</p>
            <h1>AI 团队总览</h1>
            <p className="hero-sub">
              一览已经投入工作的 Agent、真实使用情况和最新活动，了解这支 AI 团队此刻如何运转。
            </p>
          </div>
          <div className={`team-light ${hasIncident ? "incident" : "nominal"}`}>
            <span aria-hidden="true" />
            {overview
              ? hasIncident
                ? `${overview.summary.degraded_agents + overview.summary.offline_agents} 个 Agent 需要关注`
                : `${overview.summary.running_agents} 个 Agent 正在运行`
              : "正在读取团队状态"}
          </div>
        </section>

        {degraded && (
          <div className="banner error-banner" role="status">
            Platform 接口暂不可用，当前保留最后一次成功读取的团队数据并继续重试。
          </div>
        )}
        {overview && !overview.runtime_source.healthy && (
          <div className="banner source-banner" role="status">
            Agent 运行状态暂时无法更新，使用数据仍可正常查看。
          </div>
        )}
        {overview && (!overview.usage_source.healthy || overview.usage_source.stale) && (
          <div className="banner source-banner" role="status">
            对话数据暂时无法更新，当前显示最后一次成功读取的真实数据，不会用模拟数据补齐。
          </div>
        )}

        {overview ? (
          <>
            <section className="summary-section" aria-label="AI 团队摘要">
              <div className="section-heading">
                <div>
                  <p>团队规模与使用</p>
                  <h2>今天的 AI 团队</h2>
                </div>
                <span>最后更新 {formatCheckedAt(overview.runtime_source.checked_at)}</span>
              </div>
              <div className="fleet-summary-grid">
                <article className="fleet-summary-card">
                  <span>已开发 Agent</span>
                  <strong>{formatCount(overview.summary.total_agents)}</strong>
                  <p>已纳入平台观察</p>
                </article>
                <article className="fleet-summary-card summary-running">
                  <span>运行中</span>
                  <strong>{formatCount(overview.summary.running_agents)}</strong>
                  <p>{overview.summary.active_agents} 个最近有真实活动</p>
                </article>
                <article className="fleet-summary-card summary-total">
                  <span>累计对话</span>
                  <strong>{formatCount(overview.summary.total_conversations)}</strong>
                  <p>来自已接入的数据飞轮</p>
                </article>
                <article className="fleet-summary-card summary-weekly">
                  <span>近 7 天对话</span>
                  <strong>{formatCount(overview.summary.conversations_last_7d)}</strong>
                  <p>{formatChange(overview.summary.change_percent)}</p>
                </article>
              </div>
            </section>

            {usageReadable ? (
              <section className="insight-grid" aria-label="团队使用洞察">
                <UsageTrend trend={overview.trend} />
                <ActiveRanking agents={overview.agents} />
              </section>
            ) : (
              <section className="usage-unavailable-card" aria-label="团队使用洞察">
                <span aria-hidden="true">◎</span>
                <div><h2>对话数据暂不可用</h2><p>运行状态仍在正常更新，Platform 会继续尝试读取数据飞轮。</p></div>
              </section>
            )}

            <section className="agents-section" aria-label="Agent 团队">
              <div className="section-heading">
                <div>
                  <p>团队成员</p>
                  <h2>所有 Agent</h2>
                </div>
                <span>{overview.agents.length} 个 Agent · 每 10 秒自动刷新</span>
              </div>
              <div className="fleet-agent-grid">
                {overview.agents.map((agent) => (
                  <FleetAgentCard agent={agent} key={agent.id} now={now} />
                ))}
              </div>
            </section>
          </>
        ) : (
          <section className="empty-state" aria-live="polite">
            <span className="empty-pulse" aria-hidden="true" />
            <h2>{degraded ? "暂时无法读取 AI 团队" : "正在建立团队视图"}</h2>
            <p>{degraded ? "Platform 会继续自动重试。" : "正在汇总 Agent 状态和真实对话数据。"}</p>
          </section>
        )}
      </main>

      <footer className="site-foot">
        <span>Orbbec Agent Platform</span><span className="dot">·</span><span>只读展示，不控制 Agent</span>
      </footer>
    </div>
  );
}
