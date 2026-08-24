/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type { Conversation } from "../conversationTypes";
import { BrainWorkspacePage } from "./BrainWorkspacePage";

const account: Account = {
  internal_user_id: "member", display_name: "洛奇", role: "member", departments: [], gender: null,
  observation_agent_ids: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
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
    await act(async () => container.querySelector<HTMLAnchorElement>('a[href="/conversations/newer"]')?.click());
    expect(onNavigate).toHaveBeenCalledWith("/conversations/newer");
  });

  it("does not block the composer when Session history is unavailable", async () => {
    const list = vi.fn().mockRejectedValue(new TypeError("offline"));
    await act(async () => root.render(<BrainWorkspacePage account={account} client={{ list }} />));
    expect(container.textContent).toContain("对话列表暂时无法读取");
    expect(container.querySelector("textarea[aria-label='你想完成什么？']")).not.toBeNull();
  });
});
