/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "../../conversationTypes";
import { ConversationSidebar } from "./ConversationSidebar";

const selected: Conversation = {
  conversation_id: "selected", mode: "brain", direct_agent_id: null, title: "当前会话",
  status: "active", summary_through_seq: 0, created_at: "2026-08-24T01:00:00Z",
  updated_at: "2026-08-24T02:00:00Z", archived_at: null,
};
const direct: Conversation = {
  ...selected, conversation_id: "direct", mode: "direct_agent", direct_agent_id: "hr-bot",
  title: "候选人搜寻", updated_at: "2026-08-24T01:30:00Z", activity_status: "completed", unread: true,
};

describe("ConversationSidebar", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("renders one selectable row per Session and delegates actions", async () => {
    const onSelect = vi.fn(); const onNewConversation = vi.fn(); const onLoadMore = vi.fn();
    await act(async () => root.render(<ConversationSidebar
      conversations={[selected, direct]} selectedConversationId="selected"
      loading={false} error={false} hasMore loadingMore={false} mobileOpen={false}
      onCloseMobile={vi.fn()} onLoadMore={onLoadMore} onNewConversation={onNewConversation}
      onRetry={vi.fn()} onOpenConversation={onSelect}
      conversationHref={(conversationId) => `/hr/conversations/${encodeURIComponent(conversationId)}`}
    />));

    expect(container.querySelector('nav[aria-label="对话列表"]')).not.toBeNull();
    expect(container.querySelector(".conversation-sidebar-head strong")?.textContent).toBe("Agent 大脑");
    expect(container.querySelector('a[aria-current="page"]')?.textContent).toContain("当前会话");
    expect(container.textContent).not.toContain("HR Agent");
    expect(container.querySelector('a[href="/hr/conversations/direct"]')).not.toBeNull();
    expect(container.querySelector('a[href="/hr/conversations/direct"]')?.textContent).toContain("已完成");
    expect(container.querySelector('a[href="/hr/conversations/direct"] [aria-label="有未读更新"]')).not.toBeNull();
    await act(async () => container.querySelector<HTMLAnchorElement>('a[href="/hr/conversations/direct"]')?.click());
    expect(onSelect).toHaveBeenCalledWith("direct");
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-sidebar-new")?.click());
    expect(onNewConversation).toHaveBeenCalledTimes(1);
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-sidebar-more")?.click());
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("offers accessible rename, archive, archived history, and restore actions", async () => {
    const onRename = vi.fn();
    const onArchive = vi.fn();
    const onLoadArchived = vi.fn();
    const onRestore = vi.fn();
    const archived = { ...selected, conversation_id: "archived", title: "已归档", status: "archived" as const, archived_at: "2026-08-25T10:00:00Z" };
    await act(async () => root.render(<ConversationSidebar
      archivedConversations={[archived]}
      conversations={[selected]}
      selectedConversationId="selected"
      loading={false} error={false} hasMore={false} loadingMore={false} mobileOpen={false}
      onArchive={onArchive} onCloseMobile={vi.fn()} onLoadArchived={onLoadArchived}
      onLoadMore={vi.fn()} onNewConversation={vi.fn()} onRename={onRename}
      onRestore={onRestore} onRetry={vi.fn()} onOpenConversation={vi.fn()}
      conversationHref={(conversationId) => `/conversations/${conversationId}`}
    />));

    const actions = container.querySelector<HTMLButtonElement>("button[aria-label='打开对话操作']");
    expect(actions).not.toBeNull();
    await act(async () => actions?.click());
    await act(async () => container.querySelector<HTMLButtonElement>("button[data-action='rename']")?.click());
    const input = container.querySelector<HTMLInputElement>("input[aria-label='对话标题']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "新的标题");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLFormElement>("form.conversation-rename")?.requestSubmit());
    expect(onRename).toHaveBeenCalledWith("selected", "新的标题");

    await act(async () => actions?.click());
    await act(async () => container.querySelector<HTMLButtonElement>("button[data-action='archive']")?.click());
    expect(onArchive).toHaveBeenCalledWith("selected");
    await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='查看已归档对话']")?.click());
    expect(onLoadArchived).toHaveBeenCalledTimes(1);
    await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='恢复已归档']")?.click());
    expect(onRestore).toHaveBeenCalledWith("archived");
    expect(container.textContent).not.toContain("删除");
  });
});
