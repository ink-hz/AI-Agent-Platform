import { useEffect, useMemo, useState } from "react";

import { fetchAgentCatalog } from "../brainApi";
import type { AgentCapabilityCard } from "../brainTypes";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";

const WORKSPACE_URLS: Readonly<Record<string, string>> = Object.freeze({
  "ai-admin-agent": "/office/?view=services",
  "ai-fae-agent": "https://fae.orbbec.com.cn/",
});

function groupName(card: AgentCapabilityCard): string {
  return card.interaction_modes.includes("external_workspace") ? "专业工作区" : card.domain_group;
}

function safeWorkspaceUrl(card: AgentCapabilityCard): string | null {
  const expected = WORKSPACE_URLS[card.agent_id];
  return expected && card.workspace_url === expected ? expected : null;
}

function AgentCard({ card }: { card: AgentCapabilityCard }) {
  const content = <><span>{card.domain_group}</span><h3>{card.display_name}</h3><p>{card.mission}</p>
    <ul>{card.capabilities.slice(0, 3).map((capability) => <li key={capability}>{capability}</li>)}</ul></>;
  if (!card.interaction_modes.includes("external_workspace")) {
    return <PlatformLink className="agent-use-card" href={`/agents/${encodeURIComponent(card.agent_id)}`}>
      {content}<b>直接使用 →</b>
    </PlatformLink>;
  }
  const href = safeWorkspaceUrl(card);
  if (!href) return <article className="agent-use-card agent-use-card-disabled">{content}<b>入口暂不可用</b></article>;
  return <a className="agent-use-card" href={href}>{content}<b>打开工作区 →</b></a>;
}


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
    for (const agent of agents ?? []) {
      const group = groupName(agent);
      result.set(group, [...(result.get(group) ?? []), agent]);
    }
    const order = ["HR", "Marketing", "专业工作区"];
    return [...result.entries()].sort(([left], [right]) => {
      const leftIndex = order.indexOf(left); const rightIndex = order.indexOf(right);
      return (leftIndex < 0 ? order.length : leftIndex) - (rightIndex < 0 ? order.length : rightIndex)
        || left.localeCompare(right, "zh-CN");
    });
  }, [agents]);

  return <div className="agent-use-directory">
    <section className="use-page-intro"><p>AUTHORIZED EXPERTS</p><h1>专业 Agent</h1><span>直接进入你已获授权的专业能力。每次任务仍由 Platform 保存、鉴权和回放。</span></section>
    {error ? <ErrorState onRetry={() => setAttempt((value) => value + 1)} />
      : agents === null ? <LoadingState label="正在读取可用 Agent" />
      : agents.length === 0 ? <EmptyState title="暂时没有可用的专业 Agent" description="你仍可从 Agent 大脑完成通用对话和需求澄清。" />
      : <div className="agent-use-groups">{groups.map(([group, cards]) => <section key={group}>
        <h2>{group}</h2>
        <div className="agent-use-grid">{cards.map((card) => <AgentCard card={card} key={card.agent_id} />)}</div>
      </section>)}</div>}
  </div>;
}
