import { useEffect, useRef, useState, type FormEvent, type MouseEvent } from "react";

import { platformPath } from "../../auth";
import type { Conversation } from "../../conversationTypes";

export interface ConversationSidebarProps {
  title?: string;
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
  archivedConversations?: Conversation[];
  onArchive?(conversationId: string): void | Promise<void>;
  onLoadArchived?(): void | Promise<void>;
  onRename?(conversationId: string, title: string): void | Promise<void>;
  onRestore?(conversationId: string): void | Promise<void>;
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
  title = "Agent 大脑",
  conversations, selectedConversationId, loading, error, hasMore, loadingMore, mobileOpen,
  onCloseMobile, onLoadMore, onNewConversation, onRetry, onSelect,
  archivedConversations = [], onArchive, onLoadArchived, onRename, onRestore,
}: ConversationSidebarProps) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [archivedOpen, setArchivedOpen] = useState(false);
  useEffect(() => {
    if (mobileOpen) closeButton.current?.focus();
  }, [mobileOpen]);
  const submitRename = (event: FormEvent) => {
    event.preventDefault();
    const selected = renameTitle.trim();
    if (!renamingId || !selected || !onRename) return;
    void onRename(renamingId, selected);
    setRenamingId(null); setMenuId(null);
  };
  return <aside
    aria-label={mobileOpen ? "对话列表" : "对话列表面板"}
    aria-modal={mobileOpen ? "true" : undefined}
    className={`conversation-sidebar${mobileOpen ? " is-open" : ""}`}
    role={mobileOpen ? "dialog" : undefined}
  >
    <div className="conversation-sidebar-head">
      <strong>{title}</strong>
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
        const href = `/conversations/${encodeURIComponent(conversation.conversation_id)}`;
        return <div className="conversation-sidebar-row" key={conversation.conversation_id}>
          <a
            aria-current={selected ? "page" : undefined}
            className="conversation-session-link"
            href={platformPath(href)}
            onClick={(event) => {
              if (!normalClick(event)) return;
              event.preventDefault(); onSelect(conversation.conversation_id); onCloseMobile();
            }}
          >
            <strong>{conversation.title}</strong>
            <time dateTime={conversation.updated_at}>{timeLabel(conversation.updated_at)}</time>
          </a>
          {selected && (onRename || onArchive) && <div className="conversation-row-actions">
            <button aria-expanded={menuId === conversation.conversation_id} aria-label="打开对话操作" onClick={() => setMenuId((current) => current === conversation.conversation_id ? null : conversation.conversation_id)} type="button">•••</button>
            {menuId === conversation.conversation_id && <div className="conversation-action-menu">
              {onRename && <button data-action="rename" onClick={() => { setRenamingId(conversation.conversation_id); setRenameTitle(conversation.title); }} type="button">重命名</button>}
              {onArchive && <button data-action="archive" onClick={() => { void onArchive(conversation.conversation_id); setMenuId(null); }} type="button">归档</button>}
            </div>}
          </div>}
          {renamingId === conversation.conversation_id && <form className="conversation-rename" onSubmit={submitRename}>
            <input aria-label="对话标题" maxLength={160} onChange={(event) => setRenameTitle(event.target.value)} value={renameTitle} />
            <button type="submit">保存</button>
            <button onClick={() => setRenamingId(null)} type="button">取消</button>
          </form>}
        </div>;
      })}</div>
      {hasMore && <button className="conversation-sidebar-more" disabled={loadingMore} onClick={onLoadMore} type="button">
        {loadingMore ? "正在读取…" : "加载更早对话"}
      </button>}
      {error && conversations.length > 0 && <p className="conversation-sidebar-state" role="alert">更早对话暂时无法读取</p>}
      {onLoadArchived && <section className="conversation-archive-section">
        <button aria-expanded={archivedOpen} aria-label="查看已归档对话" onClick={() => {
          const next = !archivedOpen; setArchivedOpen(next); if (next) void onLoadArchived();
        }} type="button">已归档</button>
        {archivedOpen && <div>{archivedConversations.length === 0
          ? <p>暂无已归档对话</p>
          : archivedConversations.map((conversation) => <div className="conversation-archived-row" key={conversation.conversation_id}>
            <span>{conversation.title}</span><time dateTime={conversation.updated_at}>{timeLabel(conversation.updated_at)}</time>
            {onRestore && <button aria-label={`恢复${conversation.title}`} onClick={() => void onRestore(conversation.conversation_id)} type="button">恢复</button>}
          </div>)}</div>}
      </section>}
    </nav>
  </aside>;
}
