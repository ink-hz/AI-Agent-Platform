import { useEffect, useRef, type MouseEvent } from "react";

import { platformPath } from "../../auth";
import type { Conversation } from "../../conversationTypes";
import { professionalAgentLabel } from "./agentLabels";

export interface ConversationSidebarProps {
  conversations: Conversation[];
  selectedConversationId?: string;
  loading: boolean;
  error: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  mobileOpen: boolean;
  onCloseMobile(): void;
  onLoadMore(): void;
  onNewConversation(): void;
  onRetry(): void;
  onSelect(conversationId: string): void;
}

function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function normalClick(event: MouseEvent<HTMLAnchorElement>): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

export function ConversationSidebar({
  conversations, selectedConversationId, loading, error, hasMore, loadingMore, mobileOpen,
  onCloseMobile, onLoadMore, onNewConversation, onRetry, onSelect,
}: ConversationSidebarProps) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (mobileOpen) closeButton.current?.focus();
  }, [mobileOpen]);
  return <aside
    aria-label={mobileOpen ? "对话列表" : "对话列表面板"}
    aria-modal={mobileOpen ? "true" : undefined}
    className={`conversation-sidebar${mobileOpen ? " is-open" : ""}`}
    role={mobileOpen ? "dialog" : undefined}
  >
    <div className="conversation-sidebar-head">
      <strong>Agent 大脑</strong>
      <button aria-label="关闭对话列表" className="conversation-sidebar-close" onClick={onCloseMobile} ref={closeButton} type="button">×</button>
    </div>
    <button className="conversation-sidebar-new" onClick={onNewConversation} type="button">＋ 新对话</button>
    <nav aria-label="对话列表">
      {loading && conversations.length === 0 && <p className="conversation-sidebar-state" role="status">正在读取对话…</p>}
      {error && conversations.length === 0 && <div className="conversation-sidebar-state" role="alert">
        <span>对话列表暂时无法读取</span><button onClick={onRetry} type="button">重试</button>
      </div>}
      {!loading && !error && conversations.length === 0 && <p className="conversation-sidebar-state">还没有对话</p>}
      <div className="conversation-sidebar-list">{conversations.map((conversation) => {
        const selected = conversation.conversation_id === selectedConversationId;
        const agent = conversation.mode === "direct_agent"
          ? professionalAgentLabel(conversation.direct_agent_id) ?? "专业 Agent"
          : null;
        const href = `/conversations/${encodeURIComponent(conversation.conversation_id)}`;
        return <a
          aria-current={selected ? "page" : undefined}
          className="conversation-session-link"
          href={platformPath(href)}
          key={conversation.conversation_id}
          onClick={(event) => {
            if (!normalClick(event)) return;
            event.preventDefault(); onSelect(conversation.conversation_id); onCloseMobile();
          }}
        >
          <strong>{conversation.title}</strong>
          <span>{agent && <b>{agent}</b>}<time dateTime={conversation.updated_at}>{timeLabel(conversation.updated_at)}</time></span>
        </a>;
      })}</div>
      {hasMore && <button className="conversation-sidebar-more" disabled={loadingMore} onClick={onLoadMore} type="button">
        {loadingMore ? "正在读取…" : "加载更早对话"}
      </button>}
      {error && conversations.length > 0 && <p className="conversation-sidebar-state" role="alert">更早对话暂时无法读取</p>}
    </nav>
  </aside>;
}
