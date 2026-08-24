import { useEffect, useMemo, useState } from "react";

import { fetchAgentCatalog } from "../brainApi";
import type { AgentCapabilityCard } from "../brainTypes";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";

const WORKSPACE_URLS: Readonly<Record<string, string>> = Object.freeze({
  "ai-admin-agent": "/office/?view=services",
  "ai-fae-agent": "https://fae.orbbec.com.cn/",
});
const AGENT_ORDER = Object.freeze([
  "ai-fae-agent",
  "hr-bot",
  "marketing-prospecting-bot",
  "marketing-inbound-bot",
  "marketing-voice-bot",
  "marketing-intelligence-bot",
  "marketing-gtm-bot",
  "ai-admin-agent",
]);

type AgentKind = "fae" | "hr" | "marketing" | "admin";

function agentKind(card: AgentCapabilityCard): AgentKind {
  if (card.agent_id === "ai-fae-agent") return "fae";
  if (card.agent_id === "ai-admin-agent") return "admin";
  return card.domain_group === "Marketing" ? "marketing" : "hr";
}

function safeWorkspaceUrl(card: AgentCapabilityCard): string | null {
  const expected = WORKSPACE_URLS[card.agent_id];
  return expected && card.workspace_url === expected ? expected : null;
}

function AgentCard({ card }: { card: AgentCapabilityCard }) {
  const external = card.interaction_modes.includes("external_workspace");
  const action = external ? "打开工作区" : "进入 Agent";
  const kind = agentKind(card);
  const content = <>
    <header className="agent-use-card-head">
      <span>{card.domain_group}</span>
      <span aria-hidden="true" className="agent-use-card-arrow">↗</span>
    </header>
    <div className="agent-use-card-body">
      <h3>{card.display_name}</h3>
      <p>{card.mission}</p>
      <ul>{card.capabilities.slice(0, 3).map((capability) => <li key={capability}>{capability}</li>)}</ul>
    </div>
  </>;
  const footer = <span className="agent-use-card-action">{action}<span aria-hidden="true">→</span></span>;
  if (!card.interaction_modes.includes("external_workspace")) {
    return <PlatformLink aria-label={`进入 ${card.display_name}`} className="agent-use-card"
      data-agent-kind={kind} href={`/agents/${encodeURIComponent(card.agent_id)}`}>
      {content}{footer}
    </PlatformLink>;
  }
  const href = safeWorkspaceUrl(card);
  if (!href) return <article className="agent-use-card agent-use-card-disabled" data-agent-kind={kind}>
    {content}<span className="agent-use-card-unavailable">入口暂不可用</span>
  </article>;
  return <a aria-label={`打开 ${card.display_name} 工作区`} className="agent-use-card"
    data-agent-kind={kind} href={href}>{content}{footer}</a>;
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
  const orderedAgents = useMemo(() => [...(agents ?? [])].sort((left, right) => {
    const leftIndex = AGENT_ORDER.indexOf(left.agent_id);
    const rightIndex = AGENT_ORDER.indexOf(right.agent_id);
    return (leftIndex < 0 ? AGENT_ORDER.length : leftIndex) - (rightIndex < 0 ? AGENT_ORDER.length : rightIndex)
      || left.display_name.localeCompare(right.display_name, "zh-CN");
  }), [agents]);

  return <div className="agent-use-directory">
    <section className="use-page-intro"><p>AUTHORIZED EXPERTS</p><h1>专业 Agent</h1><span>直接进入你已获授权的专业能力。每次任务仍由 Platform 保存、鉴权和回放。</span></section>
    {error ? <ErrorState onRetry={() => setAttempt((value) => value + 1)} />
      : agents === null ? <LoadingState label="正在读取可用 Agent" />
      : agents.length === 0 ? <EmptyState title="暂时没有可用的专业 Agent" description="你仍可从 Agent 大脑完成通用对话和需求澄清。" />
      : <div className="agent-use-grid agent-use-directory-grid">
        {orderedAgents.map((card) => <AgentCard card={card} key={card.agent_id} />)}
      </div>}
  </div>;
}
