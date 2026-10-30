import { PlatformPageHeader } from "../platform/PlatformPageHeader";


import { BUSINESS_AGENT_LABELS } from "../platform/agentLabels";
import { WebCatalogProvider, useWebCatalog, type CatalogLoader } from "../platform/WebCatalog";
import { webEntryPath } from "../platform/webEntryPolicy";
import type { AgentCapabilityCard } from "../brainTypes";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";

type AgentKind = "fae" | "hr" | "voc" | "admin" | "service";
function agentKind(card: AgentCapabilityCard): AgentKind {
  if (["ai-fae-agent", "ai-daq-fae-agent"].includes(card.agent_id)) return "fae";
  if (card.agent_id === "hr-bot") return "hr";
  if (card.agent_id === "ai-admin-agent") return "admin";
  return card.agent_id === "voc" ? "voc" : "service";
}

function AgentCard({ card }: { card: AgentCapabilityCard }) {
  const displayName = BUSINESS_AGENT_LABELS[card.agent_id] ?? card.display_name;
  const kind = agentKind(card);
  const content = <>
    <header className="agent-use-card-head">
      <span>{card.domain_group}</span>
      <span aria-hidden="true" className="agent-use-card-arrow">↗</span>
    </header>
    <div className="agent-use-card-body">
      <h3>{displayName}</h3>
      <p>{card.mission}</p>
      {card.capabilities[0] && <p className="agent-use-card-capability">{card.capabilities[0]}</p>}
    </div>
  </>;
  const footer = <span className="agent-use-card-action">
    <span className="agent-use-card-availability">可用</span>
    <span className="agent-use-card-open">打开 <span aria-hidden="true">→</span></span>
  </span>;
  const href = webEntryPath(card);
  if (!href) return <article className="agent-use-card agent-use-card-disabled" data-agent-kind={kind}>
    {content}<span className="agent-use-card-unavailable">入口暂不可用</span>
  </article>;
  return <a aria-label={`打开 ${displayName} 工作区`} className="agent-use-card"
    data-agent-kind={kind} href={href}>{content}{footer}</a>;
}


export function AgentUseDirectoryPage({ loadCatalog }: { loadCatalog?: CatalogLoader }) {
  const catalog = useWebCatalog();
  if (!catalog) return <WebCatalogProvider identityKey="standalone-directory" loadCatalog={loadCatalog}><AgentUseDirectoryPage /></WebCatalogProvider>;
  const {applications, status, retry} = catalog;
  return <div className="agent-use-directory">
    <PlatformPageHeader className="use-page-intro" title="Agent 目录" description="选择正式 Web 应用开始工作。" />
    {status === "error" ? <ErrorState onRetry={retry} />
      : status === "loading" ? <LoadingState label="正在读取可用 Agent" />
      : applications.length === 0 ? <EmptyState title="暂时没有可用的专业 Agent" description="当前账号暂无已授权的正式 Web 应用。" />
      : <div className="agent-use-grid agent-use-directory-grid">
        {applications.map(card => <AgentCard card={card} key={card.agent_id} />)}
      </div>}
  </div>;
}
