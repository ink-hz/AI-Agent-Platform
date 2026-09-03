/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type { Conversation, ConversationDetail } from "../conversationTypes";
import { BrainWorkspacePage } from "./BrainWorkspacePage";

const account: Account = {
  internal_user_id: "member", display_name: "洛奇", role: "member", departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
};
const older: Conversation = {
  conversation_id: "older", mode: "brain", direct_agent_id: null, title: "较早会话", status: "active",
  summary_through_seq: 0, created_at: "2026-08-24T01:00:00Z", updated_at: "2026-08-24T01:00:00Z", archived_at: null,
};
const newer: Conversation = {
  ...older, conversation_id: "newer", title: "最新会话", updated_at: "2026-08-24T02:00:00Z",
};

describe("BrainWorkspacePage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("keeps newest Sessions on the left and the new conversation composer on the right", async () => {
    const list = vi.fn().mockResolvedValue({ items: [older, newer], next_cursor: "cursor-1" });
    const onNavigate = vi.fn();
    await act(async () => root.render(<BrainWorkspacePage account={account} client={{ list }} onNavigate={onNavigate} />));

    const rows = [...container.querySelectorAll(".conversation-session-link")];
    expect(rows.map((row) => row.textContent)).toEqual([expect.stringContaining("最新会话"), expect.stringContaining("较早会话")]);
    expect(container.querySelector("textarea[aria-label='你想完成什么？']")).not.toBeNull();
    expect(container.querySelector('.brain-ai-notes-entry[href="/ai-notes"]')).not.toBeNull();
    await act(async () => container.querySelector<HTMLAnchorElement>('a[href="/conversations/newer"]')?.click());
    expect(onNavigate).toHaveBeenCalledWith("/conversations/newer");
  });

  it("does not block the composer when Session history is unavailable", async () => {
    const list = vi.fn().mockRejectedValue(new TypeError("offline"));
    await act(async () => root.render(<BrainWorkspacePage account={account} client={{ list }} />));
    expect(container.textContent).toContain("对话列表暂时无法读取");
    expect(container.querySelector("textarea[aria-label='你想完成什么？']")).not.toBeNull();
  });

  it("renders a selected Session inside the same workspace", async () => {
    const conversationClient = {
      fetchConversation: vi.fn().mockResolvedValue({ conversation: newer, current_turn: null } satisfies ConversationDetail),
      fetchMessages: vi.fn().mockResolvedValue([]),
      fetchTaskDetail: vi.fn(),
      createMessageSubmission: vi.fn(), streamEvents: vi.fn().mockResolvedValue(undefined),
      cancelCurrentTurn: vi.fn(), confirmAction: vi.fn(), rejectAction: vi.fn(),
      submitFeedback: vi.fn(), reconnectDelay: vi.fn().mockResolvedValue(undefined),
      retryTurn: vi.fn(),
    };
    await act(async () => root.render(<BrainWorkspacePage
      account={account} conversationId="newer" conversationClient={conversationClient}
      client={{ list: vi.fn().mockResolvedValue({ items: [newer], next_cursor: null }) }}
    />));

    expect(container.querySelector('a[aria-current="page"]')?.textContent).toContain("最新会话");
    expect(container.querySelector(".conversation-header h1")?.textContent).toBe("Agent 大脑");
    expect(container.querySelector(".conversation-page")?.textContent).not.toContain("最新会话");
    expect(container.textContent).not.toContain("← 历史对话");
    expect(container.querySelector(".brain-ai-notes-entry")).toBeNull();
  });

  it("opens and closes the mobile Session drawer without changing the conversation", async () => {
    await act(async () => root.render(<BrainWorkspacePage
      account={account} client={{ list: vi.fn().mockResolvedValue({ items: [newer], next_cursor: null }) }}
    />));
    const opener = container.querySelector<HTMLButtonElement>('button[aria-label="打开对话列表"]')!;
    expect(opener.getAttribute("aria-expanded")).toBe("false");
    await act(async () => opener.click());
    expect(opener.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[role="dialog"][aria-label="对话列表"]')).not.toBeNull();
    expect(document.activeElement).toBe(container.querySelector(".conversation-sidebar-close"));
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    expect(opener.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[role="dialog"][aria-label="对话列表"]')).toBeNull();
    expect(container.querySelector("textarea[aria-label='你想完成什么？']")).not.toBeNull();
  });
});
