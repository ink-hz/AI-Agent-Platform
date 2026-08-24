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
  title: "候选人搜寻", updated_at: "2026-08-24T01:30:00Z",
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
      onRetry={vi.fn()} onSelect={onSelect}
    />));

    expect(container.querySelector('nav[aria-label="对话列表"]')).not.toBeNull();
    expect(container.querySelector(".conversation-sidebar-head strong")?.textContent).toBe("Agent 大脑");
    expect(container.querySelector('a[aria-current="page"]')?.textContent).toContain("当前会话");
    expect(container.textContent).toContain("HR Agent");
    await act(async () => container.querySelector<HTMLAnchorElement>('a[href="/conversations/direct"]')?.click());
    expect(onSelect).toHaveBeenCalledWith("direct");
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-sidebar-new")?.click());
    expect(onNewConversation).toHaveBeenCalledTimes(1);
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-sidebar-more")?.click());
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });
});
