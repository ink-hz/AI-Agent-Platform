import { useCallback, useEffect, useState } from "react";

import type { Account } from "../auth";
import { listConversations } from "../conversationApi";
import type { Conversation, ConversationPage } from "../conversationTypes";
import { ConversationSidebar } from "../components/conversation/ConversationSidebar";
import { navigate } from "../router";
import { BrainPage, type BrainPageClient } from "./BrainPage";
import { ConversationPage as ConversationThread, type ConversationPageClient } from "./ConversationPage";

export interface BrainWorkspacePageClient {
  list(signal?: AbortSignal, before?: string): Promise<ConversationPage>;
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
      conversations={conversations}
      selectedConversationId={conversationId}
      loading={loading}
      error={error}
      hasMore={cursor !== null}
      loadingMore={loadingMore}
      mobileOpen={mobileOpen}
      onCloseMobile={() => setMobileOpen(false)}
      onLoadMore={() => void loadMore()}
      onNewConversation={() => { setMobileOpen(false); onNavigate("/"); }}
      onRetry={() => setAttempt((value) => value + 1)}
      onSelect={(selected) => onNavigate(`/conversations/${encodeURIComponent(selected)}`)}
    />
    <section className="brain-workspace-main">
      {conversationId
        ? <ConversationThread
          account={account} client={conversationClient} conversationId={conversationId}
          onConversationUpdated={upsertConversation}
        />
        : <BrainPage
          account={account} client={brainClient} onConversationCreated={upsertConversation}
          onOpenConversation={onNavigate}
        />}
    </section>
  </div>;
}
