import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import { directConversationPath, MARKETING_AGENT_ID_BY_SLUG } from "../../platform/workspaces";
import type { MarketingAgentSlug } from "../../router";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import { DirectAgentWorkspace } from "../direct/DirectAgentWorkspace";


const MARKETING_AGENTS: ReadonlyArray<{ label: string; slug: MarketingAgentSlug }> = [
  { label: "Prospecting", slug: "prospecting" },
  { label: "Inbound", slug: "inbound" },
  { label: "Voice", slug: "voice" },
  { label: "Intelligence", slug: "intelligence" },
  { label: "GTM", slug: "gtm" },
];


function MarketingAgentSwitcher({ selected }: { selected: MarketingAgentSlug }) {
  return <nav aria-label="Marketing Agent 切换" className="agent-switcher">
    {MARKETING_AGENTS.map(({ label, slug }) => <PlatformLink
      aria-current={slug === selected ? "page" : undefined}
      href={`/marketing/${slug}`}
      key={slug}
    >{label}</PlatformLink>)}
  </nav>;
}


function marketingConversationPath(agentId: string, agentSlug: MarketingAgentSlug, conversationId: string): string {
  return directConversationPath(agentId, conversationId)
    ?? `/marketing/${agentSlug}/conversations/${encodeURIComponent(conversationId)}`;
}


export function MarketingWorkspacePage({
  account,
  agentSlug,
  conversationId,
}: {
  account: Account;
  agentSlug: MarketingAgentSlug;
  conversationId?: string;
}) {
  const agentId = MARKETING_AGENT_ID_BY_SLUG[agentSlug];
  return <WorkspaceErrorBoundary title="Marketing Agent">
    <DirectAgentWorkspace
      key={agentId}
      account={account}
      agentId={agentId}
      conversationId={conversationId}
      conversationPath={(id) => marketingConversationPath(agentId, agentSlug, id)}
      header={<MarketingAgentSwitcher selected={agentSlug} />}
    />
  </WorkspaceErrorBoundary>;
}
