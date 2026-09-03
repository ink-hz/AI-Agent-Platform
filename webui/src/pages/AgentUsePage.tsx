import { useCallback, useEffect, useState } from "react";

import type { Account } from "../auth";
import {
  fetchAgentCatalog,
  launchAgent as issueAgentLaunch,
  type AgentLaunch,
} from "../brainApi";
import {
  listConversations,
  startConversation,
  type ConversationSubmission,
} from "../conversationApi";
import type { AgentCapabilityCard } from "../brainTypes";
import type { ConversationPage } from "../conversationTypes";
import { ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { FAE_DIRECT_PATH } from "../platform/workspaces";
import { navigate } from "../router";
import { DirectAgentWorkspace } from "../workspaces/direct/DirectAgentWorkspace";
import type { ConversationPageClient } from "./ConversationPage";


const WORKSPACE_URLS: Readonly<Record<string, string>> = Object.freeze({
  "ai-admin-agent": "/office/?view=services",
  "ai-fae-agent": FAE_DIRECT_PATH,
});

export interface AgentHistoryClient {
  list(signal?: AbortSignal, before?: string, limit?: number, directAgentId?: string, status?: "active" | "archived"): Promise<ConversationPage>;
}

const DEFAULT_HISTORY_CLIENT: AgentHistoryClient = { list: listConversations };

function scopedConversationPath(agentId: string, conversationId: string): string {
  return `/agents/${encodeURIComponent(agentId)}/conversations/${encodeURIComponent(conversationId)}`;
}

export function AgentUsePage({
  account,
  agentId,
  conversationId,
  loadCatalog = fetchAgentCatalog,
  createSubmission = startConversation,
  historyClient = DEFAULT_HISTORY_CLIENT,
  conversationClient,
  onOpenConversation = (path) => navigate(path),
  launchAgent = issueAgentLaunch,
  onLaunchReady = (url) => window.location.assign(url),
}: {
  account: Account;
  agentId: string;
  conversationId?: string;
  loadCatalog?: (signal?: AbortSignal) => Promise<AgentCapabilityCard[]>;
  createSubmission?: (text: string, csrfToken: string, agentId?: string) => ConversationSubmission;
  historyClient?: AgentHistoryClient;
  conversationClient?: ConversationPageClient;
  onOpenConversation?: (path: string) => void;
  launchAgent?: (agentId: string, csrfToken: string) => Promise<AgentLaunch>;
  onLaunchReady?: (url: string) => void;
}) {
  const [catalog, setCatalog] = useState<AgentCapabilityCard[] | null>(null);
  const [loadFailure, setLoadFailure] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchFailure, setLaunchFailure] = useState(false);
  const card = catalog?.find((item) => item.agent_id === agentId) ?? null;
  const loadedCatalog = useCallback(() => Promise.resolve(catalog ?? []), [catalog]);

  useEffect(() => {
    const controller = new AbortController();
    setLoadFailure(false);
    loadCatalog(controller.signal).then((cards) => {
      if (!controller.signal.aborted) setCatalog(cards);
    }).catch(() => { if (!controller.signal.aborted) setLoadFailure(true); });
    return () => controller.abort();
  }, [loadCatalog]);

  const openEnterpriseWorkspace = async () => {
    if (launching || account.hard_stale_read_only) return;
    setLaunching(true);
    setLaunchFailure(false);
    try {
      const launch = await launchAgent(agentId, account.csrf_token);
      onLaunchReady(launch.launch_url);
    } catch {
      setLaunchFailure(true);
    } finally {
      setLaunching(false);
    }
  };

  if (loadFailure) return <><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink><ErrorState /></>;
  if (!catalog) return <LoadingState label="正在打开专业 Agent" />;
  if (!card) return <><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink><ErrorState /></>;

  if (card.interaction_modes.includes("external_workspace")) {
    const workspace = WORKSPACE_URLS[card.agent_id] === card.workspace_url ? card.workspace_url : null;
    const enterpriseLaunch = card.agent_id === "ai-fae-agent" && workspace !== null;
    return <div className="agent-use-page"><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink>
      <section className="agent-use-profile"><span>{card.domain_group}</span><h1>{card.display_name}</h1><p>{card.mission}</p>
        {enterpriseLaunch
          ? <button className="workspace-open-button" disabled={launching || account.hard_stale_read_only}
            onClick={() => void openEnterpriseWorkspace()} type="button">
            {launching ? "正在验证企业身份…" : "使用企业身份打开 FAE →"}
          </button>
          : workspace ? <a className="workspace-open-button" href={workspace}>打开工作区 →</a> : <p role="alert">入口暂不可用</p>}
        {launchFailure && <p role="alert">暂时无法打开 FAE，请重新尝试。</p>}
      </section></div>;
  }

  return <DirectAgentWorkspace
    account={account}
    agentId={agentId}
    conversationId={conversationId}
    conversationPath={(id) => scopedConversationPath(agentId, id)}
    loadCatalog={loadedCatalog}
    createSubmission={createSubmission}
    historyClient={historyClient}
    conversationClient={conversationClient}
    onOpenConversation={onOpenConversation}
  />;
}
