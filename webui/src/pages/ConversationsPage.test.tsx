/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "../conversationTypes";
import { ConversationsPage } from "./ConversationsPage";


const first: Conversation = {
  conversation_id: "8c13c965-1b60-472e-b275-199987d1d109", mode: "brain", direct_agent_id: null,
  title: "两轮对话只有一条历史记录", status: "active", summary_through_seq: 0,
  created_at: "2026-08-23T10:00:00Z", updated_at: "2026-08-23T11:00:00Z", archived_at: null,
};
const second: Conversation = {
  ...first, conversation_id: "4e2ac19d-00cc-43ca-a953-f678b8bf7029", title: "更早的对话",
  updated_at: "2026-08-22T11:00:00Z",
};


describe("ConversationsPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("shows Conversation history newest first and paginates without duplicating a multi-turn item", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [first], next_cursor: "older" })
      .mockResolvedValueOnce({ items: [second], next_cursor: null });
    await act(async () => root.render(<ConversationsPage list={list} />));

    expect(container.querySelectorAll(".conversation-history-list > a")).toHaveLength(1);
    expect(container.textContent).toContain("两轮对话只有一条历史记录");
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-load-more")?.click());

    expect(list).toHaveBeenNthCalledWith(2, undefined, "older");
    expect(container.querySelectorAll(".conversation-history-list > a")).toHaveLength(2);
  });

  it("offers an explicit new Conversation action", async () => {
    await act(async () => root.render(<ConversationsPage list={vi.fn().mockResolvedValue({ items: [], next_cursor: null })} />));
    const link = container.querySelector<HTMLAnchorElement>("a[href='/']");
    expect(link?.textContent).toContain("新建对话");
    expect(container.textContent).toContain("还没有历史对话");
  });
});
