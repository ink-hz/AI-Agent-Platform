import { useEffect, useMemo, useState } from "react";

import { fetchAgentCatalog } from "../brainApi";
import type { AgentCapabilityCard } from "../brainTypes";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";


export function AgentUseDirectoryPage({
  loadCatalog = fetchAgentCatalog,
}: {
  loadCatalog?: (signal?: AbortSignal) => Promise<AgentCapabilityCard[]>;
}) {
  const [agents, setAgents] = useState<AgentCapabilityCard[] | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    loadCatalog(controller.signal).then(setAgents).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [attempt, loadCatalog]);
  const groups = useMemo(() => {
    const result = new Map<string, AgentCapabilityCard[]>();
    for (const agent of agents ?? []) result.set(agent.domain_group, [...(result.get(agent.domain_group) ?? []), agent]);
    return [...result.entries()];
  }, [agents]);

  return <div className="agent-use-directory">
    <section className="use-page-intro"><p>AUTHORIZED EXPERTS</p><h1>专业 Agent</h1><span>直接进入你已获授权的专业能力。每次任务仍由 Platform 保存、鉴权和回放。</span></section>
    {error ? <ErrorState onRetry={() => setAttempt((value) => value + 1)} />
      : agents === null ? <LoadingState label="正在读取可用 Agent" />
      : agents.length === 0 ? <EmptyState title="暂时没有可用的专业 Agent" description="你仍可从 Agent 大脑完成通用对话和需求澄清。" />
      : <div className="agent-use-groups">{groups.map(([group, cards]) => <section key={group}>
        <h2>{group}</h2>
        <div className="agent-use-grid">{cards.map((card) => <PlatformLink className="agent-use-card" href={`/agents/${encodeURIComponent(card.agent_id)}`} key={card.agent_id}>
          <span>{card.domain_group}</span><h3>{card.display_name}</h3><p>{card.mission}</p>
          <ul>{card.capabilities.slice(0, 3).map((capability) => <li key={capability}>{capability}</li>)}</ul>
          <b>直接使用 →</b>
        </PlatformLink>)}</div>
      </section>)}</div>}
  </div>;
}
