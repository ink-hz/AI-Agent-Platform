import { useCallback, useEffect, useState } from "react";

import type { Account } from "../auth";
import { archiveConversation, listConversations, renameConversation, restoreConversation } from "../conversationApi";
import type { Conversation, ConversationPage } from "../conversationTypes";
import { ConversationSidebar } from "../components/conversation/ConversationSidebar";
import { navigate } from "../router";
import { BrainPage, type BrainPageClient } from "./BrainPage";
import { ConversationPage as ConversationThread, type ConversationPageClient } from "./ConversationPage";

export interface BrainWorkspacePageClient {
  list(signal?: AbortSignal, before?: string, limit?: number, directAgentId?: string, status?: "active" | "archived"): Promise<ConversationPage>;
}

const DEFAULT_CLIENT: BrainWorkspacePageClient = { list: listConversations };

function mergeConversations(current: Conversation[], incoming: Conversation[]): Conversation[] {
  return [...new Map([...current, ...incoming].map((item) => [item.conversation_id, item])).values()]
    .sort((left, right) => new Date(right.updated_at).valueOf() - new Date(left.updated_at).valueOf());
}

export function BrainWorkspacePage({
  account, conversationId, client = DEFAULT_CLIENT, brainClient, conversationClient,
  onNavigate = (path) => navigate(path),
}: {
  account: Account;
  conversationId?: string;
  client?: BrainWorkspacePageClient;
  brainClient?: BrainPageClient;
  conversationClient?: ConversationPageClient;
  onNavigate?: (path: string) => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [archivedConversations, setArchivedConversations] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    void client.list(controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      setConversations((current) => mergeConversations(current, page.items));
      setCursor(page.next_cursor); setLoading(false);
    }).catch(() => {
      if (!controller.signal.aborted) { setError(true); setLoading(false); }
    });
    return () => controller.abort();
  }, [attempt, client]);

  useEffect(() => {
    if (!mobileOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [mobileOpen]);

  const upsertConversation = useCallback((conversation: Conversation) => {
    setConversations((current) => mergeConversations(current, [conversation]));
  }, []);

  const loadMore = async () => {
    if (!cursor || loadingMore) return;
    const controller = new AbortController();
    setLoadingMore(true); setError(false);
    try {
      const page = await client.list(controller.signal, cursor);
      setConversations((current) => mergeConversations(current, page.items));
      setCursor(page.next_cursor);
    } catch {
      setError(true);
    } finally {
      setLoadingMore(false);
    }
  };

  const loadArchived = async () => {
    try {
      const page = await client.list(undefined, undefined, 100, undefined, "archived");
      setArchivedConversations(page.items);
    } catch {
      setError(true);
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
    if (selectedId === conversationId) onNavigate("/");
  };

  const restoreHistory = async (selectedId: string) => {
    const restored = await restoreConversation(selectedId, account.csrf_token);
    setArchivedConversations((current) => current.filter((item) => item.conversation_id !== selectedId));
    setConversations((current) => mergeConversations(current, [restored]));
  };

  return <div className="brain-workspace">
    <button
      aria-expanded={mobileOpen}
      aria-label="打开对话列表"
      className="brain-workspace-menu"
      onClick={() => setMobileOpen(true)}
      type="button"
    >☰</button>
    {mobileOpen && <button
      aria-label="关闭对话列表"
      className="conversation-sidebar-backdrop"
      onClick={() => setMobileOpen(false)}
      type="button"
    />}
    <ConversationSidebar
      archivedConversations={archivedConversations}
      conversationHref={(selected) => `/conversations/${encodeURIComponent(selected)}`}
      conversations={conversations}
      selectedConversationId={conversationId}
      loading={loading}
      error={error}
      hasMore={cursor !== null}
      loadingMore={loadingMore}
      mobileOpen={mobileOpen}
      onCloseMobile={() => setMobileOpen(false)}
      onArchive={account.hard_stale_read_only ? undefined : archiveHistory}
      onLoadArchived={() => void loadArchived()}
      onLoadMore={() => void loadMore()}
      onNewConversation={() => { setMobileOpen(false); onNavigate("/"); }}
      onRename={account.hard_stale_read_only ? undefined : renameHistory}
      onRestore={account.hard_stale_read_only ? undefined : restoreHistory}
      onRetry={() => setAttempt((value) => value + 1)}
      onOpenConversation={(selected) => onNavigate(`/conversations/${encodeURIComponent(selected)}`)}
    />
    <section className="brain-workspace-main">
      {conversationId
        ? <ConversationThread
          account={account} assistantLabel="Agent 大脑" client={conversationClient} conversationId={conversationId}
          onConversationUpdated={upsertConversation}
        />
        : <BrainPage
          account={account} client={brainClient} onConversationCreated={upsertConversation}
          onOpenAiNotes={onNavigate}
          onOpenConversation={onNavigate}
        />}
    </section>
  </div>;
}
