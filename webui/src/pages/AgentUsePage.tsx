import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import type { Account } from "../auth";
import { fetchAgentCatalog } from "../brainApi";
import {
  archiveConversation,
  conversationInputTooLarge,
  listConversations,
  renameConversation,
  restoreConversation,
  startConversation,
  type ConversationSubmission,
} from "../conversationApi";
import type { AgentCapabilityCard } from "../brainTypes";
import type { Conversation, ConversationPage } from "../conversationTypes";
import { ConversationSidebar } from "../components/conversation/ConversationSidebar";
import { ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { navigate } from "../router";
import { ConversationPage as ConversationThread, type ConversationPageClient } from "./ConversationPage";


const WORKSPACE_URLS: Readonly<Record<string, string>> = Object.freeze({
  "ai-admin-agent": "/office/?view=services",
  "ai-fae-agent": "https://fae.orbbec.com.cn/",
});

export interface AgentHistoryClient {
  list(signal?: AbortSignal, before?: string, limit?: number, directAgentId?: string, status?: "active" | "archived"): Promise<ConversationPage>;
}

const DEFAULT_HISTORY_CLIENT: AgentHistoryClient = { list: listConversations };

function mergeConversations(current: Conversation[], incoming: Conversation[]): Conversation[] {
  return [...new Map([...current, ...incoming].map((item) => [item.conversation_id, item])).values()]
    .sort((left, right) => new Date(right.updated_at).valueOf() - new Date(left.updated_at).valueOf());
}

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
}: {
  account: Account;
  agentId: string;
  conversationId?: string;
  loadCatalog?: (signal?: AbortSignal) => Promise<AgentCapabilityCard[]>;
  createSubmission?: (text: string, csrfToken: string, agentId?: string) => ConversationSubmission;
  historyClient?: AgentHistoryClient;
  conversationClient?: ConversationPageClient;
  onOpenConversation?: (path: string) => void;
}) {
  const [catalog, setCatalog] = useState<AgentCapabilityCard[] | null>(null);
  const [loadFailure, setLoadFailure] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [archivedConversations, setArchivedConversations] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyFailure, setHistoryFailure] = useState(false);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(false);
  const retained = useRef<{ text: string; submission: ConversationSubmission } | null>(null);
  const inFlight = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const card = catalog?.find((item) => item.agent_id === agentId) ?? null;
  const inputTooLarge = conversationInputTooLarge(text.trim());

  useEffect(() => {
    const controller = new AbortController();
    setLoadFailure(false);
    loadCatalog(controller.signal).then((cards) => {
      if (!controller.signal.aborted) setCatalog(cards);
    }).catch(() => { if (!controller.signal.aborted) setLoadFailure(true); });
    return () => controller.abort();
  }, [loadCatalog]);

  useEffect(() => {
    if (!card || !card.interaction_modes.includes("direct_chat")) {
      setHistoryLoading(false);
      return;
    }
    const controller = new AbortController();
    setConversations([]); setCursor(null); setHistoryLoading(true); setHistoryFailure(false);
    void historyClient.list(controller.signal, undefined, 20, agentId).then((page) => {
      if (controller.signal.aborted) return;
      setConversations(page.items); setCursor(page.next_cursor); setHistoryLoading(false);
    }).catch(() => {
      if (!controller.signal.aborted) { setHistoryFailure(true); setHistoryLoading(false); }
    });
    return () => controller.abort();
  }, [agentId, card, historyAttempt, historyClient]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const upsertConversation = useCallback((conversation: Conversation) => {
    if (conversation.direct_agent_id === agentId) {
      setConversations((current) => mergeConversations(current, [conversation]));
    }
  }, [agentId]);

  const loadMore = async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true); setHistoryFailure(false);
    try {
      const page = await historyClient.list(undefined, cursor, 20, agentId);
      setConversations((current) => mergeConversations(current, page.items));
      setCursor(page.next_cursor);
    } catch {
      setHistoryFailure(true);
    } finally {
      setLoadingMore(false);
    }
  };

  const loadArchived = async () => {
    try {
      const page = await historyClient.list(undefined, undefined, 100, agentId, "archived");
      setArchivedConversations(page.items);
    } catch {
      setHistoryFailure(true);
    }
  };

  const renameHistory = async (selectedId: string, title: string) => {
    const updated = await renameConversation(selectedId, title, account.csrf_token);
    setConversations((current) => current.map((item) => item.conversation_id === selectedId ? updated : item));
  };

  const archiveHistory = async (selectedId: string) => {
    const archived = await archiveConversation(selectedId, account.csrf_token);
    setConversations((current) => current.filter((item) => item.conversation_id !== selectedId));
    setArchivedConversations((current) => mergeConversations(current, [archived]));
    if (selectedId === conversationId) onOpenConversation(`/agents/${encodeURIComponent(agentId)}`);
  };

  const restoreHistory = async (selectedId: string) => {
    const restored = await restoreConversation(selectedId, account.csrf_token);
    setArchivedConversations((current) => current.filter((item) => item.conversation_id !== selectedId));
    setConversations((current) => mergeConversations(current, [restored]));
  };

  const send = async () => {
    const normalized = text.trim();
    if (!card || !normalized || inputTooLarge || inFlight.current || account.hard_stale_read_only) return;
    let selected = retained.current;
    if (!selected || selected.text !== normalized) {
      selected = { text: normalized, submission: createSubmission(normalized, account.csrf_token, card.agent_id) };
      retained.current = selected;
    }
    const controller = new AbortController();
    controllerRef.current?.abort(); controllerRef.current = controller;
    inFlight.current = true; setPending(true); setFailure(false);
    try {
      const result = await selected.submission.send(controller.signal);
      retained.current = null; upsertConversation(result.conversation);
      onOpenConversation(scopedConversationPath(card.agent_id, result.conversation.conversation_id));
    } catch {
      if (!controller.signal.aborted) setFailure(true);
    } finally {
      if (controllerRef.current === controller) {
        inFlight.current = false;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  };

  const submit = (event: FormEvent) => { event.preventDefault(); void send(); };

  if (loadFailure) return <><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink><ErrorState /></>;
  if (!catalog) return <LoadingState label="正在打开专业 Agent" />;
  if (!card) return <><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink><ErrorState /></>;

  if (card.interaction_modes.includes("external_workspace")) {
    const workspace = WORKSPACE_URLS[card.agent_id] === card.workspace_url ? card.workspace_url : null;
    return <div className="agent-use-page"><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink>
      <section className="agent-use-profile"><span>{card.domain_group}</span><h1>{card.display_name}</h1><p>{card.mission}</p>
        {workspace ? <a className="workspace-open-button" href={workspace}>打开工作区 →</a> : <p role="alert">入口暂不可用</p>}
      </section></div>;
  }

  const marketing = catalog.filter((item) => item.domain_group === "Marketing" && item.interaction_modes.includes("direct_chat"));
  return <div className="brain-workspace agent-use-workspace">
    <button aria-expanded={mobileOpen} aria-label="打开对话列表" className="brain-workspace-menu" onClick={() => setMobileOpen(true)} type="button">☰</button>
    {mobileOpen && <button aria-label="关闭对话列表" className="conversation-sidebar-backdrop" onClick={() => setMobileOpen(false)} type="button" />}
    <ConversationSidebar
      archivedConversations={archivedConversations}
      title={card.display_name}
      conversations={conversations}
      selectedConversationId={conversationId}
      loading={historyLoading}
      error={historyFailure}
      hasMore={cursor !== null}
      loadingMore={loadingMore}
      mobileOpen={mobileOpen}
      onCloseMobile={() => setMobileOpen(false)}
      onArchive={account.hard_stale_read_only ? undefined : archiveHistory}
      onLoadArchived={() => void loadArchived()}
      onLoadMore={() => void loadMore()}
      onNewConversation={() => { setMobileOpen(false); onOpenConversation(`/agents/${encodeURIComponent(agentId)}`); }}
      onRename={account.hard_stale_read_only ? undefined : renameHistory}
      onRestore={account.hard_stale_read_only ? undefined : restoreHistory}
      onRetry={() => setHistoryAttempt((value) => value + 1)}
      onSelect={(selected) => onOpenConversation(scopedConversationPath(agentId, selected))}
    />
    <section className="brain-workspace-main">
      {card.domain_group === "Marketing" && marketing.length === 5 && <nav aria-label="Marketing Agent 切换" className="agent-switcher">
        {marketing.map((item) => <PlatformLink aria-current={item.agent_id === agentId ? "page" : undefined} href={`/agents/${encodeURIComponent(item.agent_id)}`} key={item.agent_id}>{item.display_name.replace("Marketing ", "")}</PlatformLink>)}
      </nav>}
      {conversationId
        ? <ConversationThread
          account={account}
          assistantLabel={card.display_name}
          client={conversationClient}
          conversationId={conversationId}
          expectedAgentId={agentId}
          onConversationUpdated={upsertConversation}
          personaSubtitle={card.persona_subtitle}
        />
        : <div className="agent-use-page"><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink>
          <section className="agent-use-profile"><span>{card.domain_group}</span><h1>{card.display_name}</h1>
            {card.persona_subtitle && <p className="agent-persona-subtitle">{card.persona_subtitle}</p>}
            <p>{card.mission}</p>
            <div><section><h2>可以完成</h2><ul>{card.capabilities.map((item) => <li key={item}>{item}</li>)}</ul></section>
              <section><h2>能力边界</h2><ul>{card.exclusions.map((item) => <li key={item}>{item}</li>)}</ul></section></div>
          </section>
          <section aria-label="常用任务" className="agent-task-starters">
            {card.example_tasks.slice(0, 4).map((example) => <button
              className="agent-task-starter"
              key={example}
              onClick={() => { setText(example); retained.current = null; setFailure(false); }}
              type="button"
            >{example}</button>)}
          </section>
          <form className="agent-direct-composer" onSubmit={submit}>
            <label htmlFor="direct-agent-request">直接交给 {card.display_name}</label>
            <textarea id="direct-agent-request" rows={5} maxLength={32 * 1024} value={text} disabled={account.hard_stale_read_only}
              placeholder={card.example_tasks[0] ?? "描述任务目标和背景…"}
              onChange={(event) => { const next = event.target.value; setText(next); if (retained.current?.text !== next.trim()) retained.current = null; setFailure(false); }} />
            <div><span>对话会持续保留在当前 Agent 的左侧历史中。</span><button disabled={!text.trim() || inputTooLarge || pending || account.hard_stale_read_only} type="submit">{pending ? "正在创建…" : "开始对话"}</button></div>
          </form>
          {inputTooLarge && <p className="mission-input-error" role="alert">输入超过 32 KiB，请精简后再提交。</p>}
          {failure && <div className="brain-submit-error" role="alert"><span>对话暂未创建成功，可安全重试。</span><button onClick={() => void send()} type="button">重新提交</button></div>}
        </div>}
    </section>
  </div>;
}
