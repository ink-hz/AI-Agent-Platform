import { PlatformPageHeader } from "../platform/PlatformPageHeader";


import { BUSINESS_AGENT_LABELS } from "../platform/agentLabels";
import { WebCatalogProvider, useWebCatalog, type CatalogLoader } from "../platform/WebCatalog";
import { webEntryPath, validWebEntry, webInventoryApplications } from "../platform/webEntryPolicy";
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
  const channel = !validWebEntry(card.web_entry);
  if (!href) return <article className="agent-use-card agent-use-card-disabled" data-agent-kind={kind}>
    {content}<span className="agent-use-card-unavailable">{channel ? "已配置 · 通过 MetaBot / 飞书使用" : "未获使用授权"}</span>
  </article>;
  return <a aria-label={`打开 ${displayName} 工作区`} className="agent-use-card"
    data-agent-kind={kind} href={href}>{content}{footer}</a>;
}


export function AgentUseDirectoryPage({ loadCatalog }: { loadCatalog?: CatalogLoader }) {
  const catalog = useWebCatalog();
  if (!catalog) return <WebCatalogProvider identityKey="standalone-directory" loadCatalog={loadCatalog}><AgentUseDirectoryPage /></WebCatalogProvider>;
  const {catalog: cards, status, retry} = catalog;
  const applications = webInventoryApplications(cards);
  const channels = cards.filter(card => !validWebEntry(card.web_entry));
  return <div className="agent-use-directory">
    <PlatformPageHeader className="use-page-intro" title="Agent 目录" description="查看已配置 Agent，按访问方式选择已授权的正式应用。" />
    {status === "error" ? <ErrorState onRetry={retry} />
      : status === "loading" ? <LoadingState label="正在读取可用 Agent" />
      : cards.length === 0 ? <EmptyState title="暂时没有可用的专业 Agent" description="当前账号暂无已授权的正式 Web 应用。" />
      : <>
        <p className="catalog-inventory">已配置 {cards.length} 个 Agent · 独立 Web 应用 {applications.length} 个 · MetaBot / 飞书 {channels.length} 个</p>
        <section data-access-method="web" aria-label="独立 Web 应用"><h2>独立 Web 应用（{applications.length}）</h2>
          <div className="agent-use-grid agent-use-directory-grid">{applications.map(card => <AgentCard card={card} key={card.agent_id} />)}</div>
        </section>
        <section data-access-method="channel" aria-label="MetaBot / 飞书 Agent"><h2>MetaBot / 飞书 Agent（{channels.length}）</h2>
          <p>渠道配置独立于 Web 服务；此处展示配置元数据，运行状态见实际观测。</p>
          <div className="agent-use-grid agent-use-directory-grid">{channels.map(card => <AgentCard card={card} key={card.agent_id} />)}</div>
        </section>
      </>}
  </div>;
}
