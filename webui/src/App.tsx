import { useEffect, useState } from "react";

import { fetchAgents, fetchHealth } from "./api";
import { statusBadge } from "./status";
import type { Agent, Health } from "./types";

export default function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [health, setHealth] = useState<Record<string, Health>>({});
  const [degraded, setDegraded] = useState(false);

  useEffect(() => {
    fetchAgents().then(setAgents).catch(() => setDegraded(true));
  }, []);

  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      try {
        const list = await fetchHealth();
        if (stopped) return;
        setHealth(Object.fromEntries(list.map((item) => [item.id, item])));
        setDegraded(false);
      } catch {
        if (!stopped) setDegraded(true);
      }
    };

    tick();
    const timer = window.setInterval(tick, 30000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Orbbec</p>
          <h1>AI Agent Platform</h1>
        </div>
        <p className="subtitle">内部智能体工作台</p>
      </header>

      {degraded && (
        <div className="banner" role="status">
          平台接口暂不可用，状态可能不是最新。
        </div>
      )}

      <section className="agent-grid" aria-label="Agent 列表">
        {agents.map((agent) => {
          const agentHealth = health[agent.id];
          const badge = statusBadge(agent, agentHealth);
          return (
            <article className="agent-card" key={agent.id}>
              <div className="card-top">
                <span className="agent-icon" aria-hidden="true">{agent.icon}</span>
                <span className={`badge ${badge.tone}`}>{badge.label}</span>
              </div>

              <div className="agent-title">
                <h2>{agent.name}</h2>
                <span>{agent.domain}</span>
              </div>

              <p className="agent-desc">{agent.description}</p>

              {agentHealth && agentHealth.metrics.length > 0 && (
                <div className="metrics" aria-label={`${agent.name} 状态指标`}>
                  {agentHealth.metrics.map((metric) => (
                    <span className="chip" key={metric.label}>
                      {metric.label}: {metric.value}
                    </span>
                  ))}
                </div>
              )}

              <div className="card-foot">
                {agent.owner && <span className="owner">负责人: {agent.owner}</span>}
                <a className="enter" href={agent.entry_url} target="_blank" rel="noreferrer">
                  进入
                </a>
              </div>
            </article>
          );
        })}
      </section>
    </main>
  );
}
