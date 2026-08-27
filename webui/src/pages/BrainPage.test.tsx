/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { ConversationApiError } from "../conversationApi";
import type { ConversationSubmissionResult } from "../conversationTypes";
import { BrainPage, type BrainPageClient } from "./BrainPage";


const account: Account = {
  internal_user_id: "member", display_name: "洛奇", role: "member",
  departments: [], gender: null,
  observation_agent_ids: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};

const result: ConversationSubmissionResult = {
  conversation: {
    conversation_id: "8c13c965-1b60-472e-b275-199987d1d109", mode: "brain", direct_agent_id: null,
    status: "active", title: "找视觉人才", summary_through_seq: 0,
    created_at: "2026-08-22T10:00:00Z", updated_at: "2026-08-22T10:00:00Z", archived_at: null,
  },
  message: {
    message_id: "message", conversation_id: "8c13c965-1b60-472e-b275-199987d1d109", seq: 1,
    role: "user", content: "找视觉人才", turn_id: "turn",
    delivery_status: "accepted", created_at: "2026-08-22T10:00:00Z", completed_at: null,
  },
  turn: {
    turn_id: "turn", conversation_id: "8c13c965-1b60-472e-b275-199987d1d109", user_message_id: "message",
    assistant_message_id: null, retry_of_turn_id: null, status: "accepted",
    created_at: "2026-08-22T10:00:00Z", updated_at: "2026-08-22T10:00:00Z",
  },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, reject, resolve };
}


describe("BrainPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("is immediately usable after account load and shows only the focused first viewport", async () => {
    const client: BrainPageClient = {
      createSubmission: vi.fn(),
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenConversation={vi.fn()} />));

    expect(container.querySelector("h1")?.textContent).toBe("Agent 大脑");
    expect(container.querySelector("textarea")?.disabled).toBe(false);
    expect(container.querySelectorAll(".brain-example")).toHaveLength(3);
    expect(container.textContent).not.toContain("最近对话");
    expect(container.textContent).not.toContain("累计对话");
    expect(container.textContent).not.toContain("运行摘要");
  });

  it("opens AI engineering notes from one quiet home entry", async () => {
    const onOpenAiNotes = vi.fn();
    await act(async () => root.render(
      <BrainPage
        account={account}
        client={{ createSubmission: vi.fn() }}
        onOpenAiNotes={onOpenAiNotes}
      />,
    ));

    const entry = container.querySelector<HTMLAnchorElement>(".brain-ai-notes-entry");
    expect(entry?.textContent).toBe("AI 工程笔记 →");
    expect(entry?.getAttribute("href")).toBe("/ai-notes");
    expect(container.querySelector(".brain-hero > span + .brain-ai-notes-entry")).toBe(entry);
    expect(entry?.nextElementSibling?.classList.contains("brain-composer")).toBe(true);

    await act(async () => entry?.click());
    expect(onOpenAiNotes).toHaveBeenCalledWith("/ai-notes");
  });

  it("submits once while pending and opens the persisted Conversation URL", async () => {
    const pending = deferred<ConversationSubmissionResult>();
    const send = vi.fn(() => pending.promise);
    const onOpenConversation = vi.fn();
    const onConversationCreated = vi.fn();
    const client: BrainPageClient = {
      createSubmission: vi.fn().mockReturnValue({ idempotencyKey: "request", send }),
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onConversationCreated={onConversationCreated} onOpenConversation={onOpenConversation} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(textarea, "找视觉人才");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const submit = container.querySelector<HTMLButtonElement>(".brain-submit")!;
    await act(async () => { submit.click(); submit.click(); });
    expect(send).toHaveBeenCalledTimes(1);

    await act(async () => { pending.resolve(result); await pending.promise; });
    expect(onConversationCreated).toHaveBeenCalledWith(result.conversation);
    expect(onOpenConversation).toHaveBeenCalledWith("/conversations/8c13c965-1b60-472e-b275-199987d1d109");
  });

  it("retries a failed submission with the same retained operation", async () => {
    const send = vi.fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(result);
    const createSubmission = vi.fn().mockReturnValue({ idempotencyKey: "same", send });
    const client: BrainPageClient = {
      createSubmission,
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenConversation={vi.fn()} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "找视觉人才");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".brain-submit")?.click());
    expect(container.textContent).toContain("对话暂未创建成功");
    await act(async () => container.querySelector<HTMLButtonElement>(".brain-retry")?.click());

    expect(createSubmission).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("shows an explicit unavailable error for a disabled Brain runtime", async () => {
    const send = vi.fn().mockRejectedValue(
      new ConversationApiError(503, { detail: "Agent Brain unavailable" }),
    );
    const client: BrainPageClient = {
      createSubmission: vi.fn().mockReturnValue({ idempotencyKey: "same", send }),
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenConversation={vi.fn()} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "找视觉人才");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".brain-submit")?.click());

    expect(container.textContent).toContain("Agent 大脑暂不可用");
    expect(container.textContent).toContain("使用同一次请求重试");
    expect(container.textContent).not.toContain("正在准备");
  });

  it("blocks text that exceeds 32 KiB after UTF-8 encoding", async () => {
    const createSubmission = vi.fn();
    const client: BrainPageClient = {
      createSubmission,
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenConversation={vi.fn()} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "人".repeat(11_000));
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(container.textContent).toContain("32 KiB");
    expect(container.querySelector<HTMLButtonElement>(".brain-submit")?.disabled).toBe(true);
    expect(createSubmission).not.toHaveBeenCalled();
  });

  it("aborts an in-flight first message when the composer unmounts", async () => {
    const pending = deferred<ConversationSubmissionResult>();
    let signal: AbortSignal | undefined;
    const client: BrainPageClient = {
      createSubmission: vi.fn().mockReturnValue({
        idempotencyKey: "request",
        send: vi.fn((selected?: AbortSignal) => { signal = selected; return pending.promise; }),
      }),
    };
    await act(async () => root.render(<BrainPage account={account} client={client} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "新任务");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".brain-submit")?.click());
    expect(signal?.aborted).toBe(false);
    await act(async () => root.render(<div>已离开</div>));
    expect(signal?.aborted).toBe(true);
  });
});
