/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type {
  Conversation,
  ConversationDetail,
  ConversationEvent,
  ConversationMessage,
  ConversationSubmissionResult,
  ConversationTurn,
} from "../conversationTypes";
import { ConversationPage, type ConversationPageClient } from "./ConversationPage";


const conversationId = "8c13c965-1b60-472e-b275-199987d1d109";
const account: Account = {
  internal_user_id: "member", display_name: "洛奇", role: "member",
  departments: [], gender: null, observation_agent_ids: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};
const conversation: Conversation = {
  conversation_id: conversationId, mode: "brain", direct_agent_id: null,
  title: "帮我找候选人", status: "active", summary_through_seq: 0,
  created_at: "2026-08-23T10:00:00Z", updated_at: "2026-08-23T10:01:00Z", archived_at: null,
};
const completedTurn: ConversationTurn = {
  turn_id: "turn-1", conversation_id: conversationId, user_message_id: "message-1",
  assistant_message_id: "message-2", status: "completed",
  retry_of_turn_id: null,
  created_at: "2026-08-23T10:00:00Z", updated_at: "2026-08-23T10:01:00Z",
};
const messages: ConversationMessage[] = [
  {
    message_id: "message-1", conversation_id: conversationId, seq: 1, role: "user",
    content: "帮我找候选人", turn_id: "turn-1", delivery_status: "completed",
    created_at: "2026-08-23T10:00:00Z", completed_at: "2026-08-23T10:00:00Z",
  },
  {
    message_id: "message-2", conversation_id: conversationId, seq: 2, role: "assistant",
    content: "## 第一轮结果\n\n- 建议从 GitHub 开始", turn_id: "turn-1", delivery_status: "completed",
    created_at: "2026-08-23T10:01:00Z", completed_at: "2026-08-23T10:01:00Z",
  },
];
const event: ConversationEvent = {
  event_id: "event-1", conversation_id: conversationId, seq: 1, turn_id: "turn-1",
  event_type: "task.dispatched",
  payload: { selected_agent_id: "hr-bot", status: "running" }, created_at: "2026-08-23T10:00:10Z",
};


function submissionResult(text: string): ConversationSubmissionResult {
  return {
    conversation: { ...conversation, title: text, updated_at: "2026-08-23T10:02:00Z" },
    message: {
      message_id: "message-3", conversation_id: conversationId, seq: 3, role: "user", content: text,
      turn_id: "turn-2", delivery_status: "accepted",
      created_at: "2026-08-23T10:02:00Z", completed_at: null,
    },
    turn: {
      turn_id: "turn-2", conversation_id: conversationId, user_message_id: "message-3",
      assistant_message_id: null, retry_of_turn_id: null, status: "accepted",
      created_at: "2026-08-23T10:02:00Z", updated_at: "2026-08-23T10:02:00Z",
    },
  };
}


function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, reject, resolve };
}


function client(overrides: Partial<ConversationPageClient> = {}): ConversationPageClient {
  return {
    fetchConversation: vi.fn().mockResolvedValue({ conversation, current_turn: completedTurn } satisfies ConversationDetail),
    fetchMessages: vi.fn().mockResolvedValue(messages),
    createMessageSubmission: vi.fn().mockImplementation((_id, text) => ({
      idempotencyKey: "same", send: vi.fn().mockResolvedValue(submissionResult(text)),
    })),
    streamEvents: vi.fn().mockImplementation(async (_id, options) => options.onEvent(event)),
    cancelCurrentTurn: vi.fn(),
    submitFeedback: vi.fn(),
    retryTurn: vi.fn().mockImplementation((_conversationId, _turnId) => ({
      idempotencyKey: "retry-same",
      send: vi.fn().mockResolvedValue(submissionResult("重试")),
    })),
    reconnectDelay: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}


async function setTextarea(container: HTMLElement, value: string): Promise<void> {
  const textarea = container.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}


describe("ConversationPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks();
  });

  it("rejects a same-owner Session that belongs to a different professional Agent", async () => {
    await act(async () => root.render(<ConversationPage
      account={account}
      client={client()}
      conversationId={conversationId}
      expectedAgentId="hr-bot"
    />));

    expect(container.textContent).toContain("暂时无法读取对话");
    expect(container.textContent).not.toContain("建议从 GitHub 开始");
  });

  it("renders Markdown, collapsed execution, and keeps the composer after a follow-up", async () => {
    const pageClient = client();
    const onConversationUpdated = vi.fn();
    await act(async () => root.render(<ConversationPage
      account={account} client={pageClient} conversationId={conversationId}
      onConversationUpdated={onConversationUpdated}
    />));

    expect(container.querySelector(".conversation-assistant h2")?.textContent).toBe("第一轮结果");
    expect(container.textContent).not.toContain("历史对话");
    expect(onConversationUpdated).toHaveBeenCalledWith(conversation);
    expect(container.querySelector<HTMLDetailsElement>(".execution-card")?.open).toBe(false);
    expect(container.textContent).toContain("HR Agent");
    expect(container.querySelector("textarea[aria-label='继续对话']")).not.toBeNull();

    await setTextarea(container, "继续给出搜索式");
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-send")?.click());

    expect(pageClient.createMessageSubmission).toHaveBeenCalledWith(conversationId, "继续给出搜索式", account.csrf_token);
    expect(onConversationUpdated).toHaveBeenLastCalledWith(expect.objectContaining({
      conversation_id: conversationId, updated_at: "2026-08-23T10:02:00Z",
    }));
    expect(container.textContent).toContain("继续给出搜索式");
    expect(container.querySelector("textarea[aria-label='继续对话']")).not.toBeNull();
  });

  it("retains one submission object when a follow-up is safely retried", async () => {
    const send = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(submissionResult("继续"));
    const createMessageSubmission = vi.fn().mockReturnValue({ idempotencyKey: "same", send });
    const pageClient = client({ createMessageSubmission });
    await act(async () => root.render(<ConversationPage account={account} client={pageClient} conversationId={conversationId} />));
    await setTextarea(container, "继续");

    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-send")?.click());
    expect(container.textContent).toContain("消息暂未发送成功");
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-retry")?.click());

    expect(createMessageSubmission).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("stops the current turn without hiding the conversation", async () => {
    const active: ConversationTurn = { ...completedTurn, assistant_message_id: null, status: "running" };
    const stream = deferred<void>();
    const cancelCurrentTurn = vi.fn().mockResolvedValue({
      conversation_id: conversationId, turn_id: "turn-1", cancel_requested: true,
    });
    const pageClient = client({
      fetchConversation: vi.fn().mockResolvedValue({ conversation, current_turn: active }),
      streamEvents: vi.fn().mockReturnValue(stream.promise), cancelCurrentTurn,
    });
    await act(async () => root.render(<ConversationPage account={account} client={pageClient} conversationId={conversationId} />));

    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-stop")?.click());

    expect(cancelCurrentTurn).toHaveBeenCalledWith(conversationId, account.csrf_token, expect.any(AbortSignal));
    expect(container.textContent).toContain("正在停止");
    expect(container.querySelector("textarea[aria-label='继续对话']")).not.toBeNull();
  });

  it("resumes SSE from the last accepted sequence after reconnect", async () => {
    const active: ConversationTurn = { ...completedTurn, assistant_message_id: null, status: "running" };
    const fetchConversation = vi.fn()
      .mockResolvedValueOnce({ conversation, current_turn: active })
      .mockResolvedValue({ conversation, current_turn: completedTurn });
    const streamEvents = vi.fn()
      .mockImplementationOnce(async (_id, options) => {
        options.onEvent(event);
        throw new TypeError("offline");
      })
      .mockImplementationOnce(async (_id, options) => {
        options.onEvent({ ...event, event_id: "event-2", seq: 2, event_type: "turn.completed" });
      });
    const reconnectDelay = vi.fn().mockResolvedValue(undefined);
    const pageClient = client({ fetchConversation, streamEvents, reconnectDelay });

    await act(async () => root.render(<ConversationPage account={account} client={pageClient} conversationId={conversationId} />));

    expect(streamEvents).toHaveBeenNthCalledWith(1, conversationId, expect.objectContaining({ after: 0 }));
    expect(streamEvents).toHaveBeenNthCalledWith(2, conversationId, expect.objectContaining({ after: 1 }));
    expect(reconnectDelay).toHaveBeenCalledTimes(1);
  });

  it("is read-only when directory freshness is hard stale", async () => {
    const pageClient = client();
    await act(async () => root.render(<ConversationPage
      account={{ ...account, hard_stale_read_only: true }} client={pageClient} conversationId={conversationId}
    />));

    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>(".conversation-send")?.disabled).toBe(true);
    expect(container.textContent).toContain("当前为只读状态");
  });

  it("labels a direct-Agent answer as the selected professional Agent", async () => {
    const directConversation = { ...conversation, mode: "direct_agent" as const, direct_agent_id: "hr-bot" };
    const pageClient = client({
      fetchConversation: vi.fn().mockResolvedValue({ conversation: directConversation, current_turn: completedTurn }),
    });
    await act(async () => root.render(<ConversationPage account={account} client={pageClient} conversationId={conversationId} />));

    expect(container.querySelector(".conversation-assistant header strong")?.textContent).toBe("HR Agent");
  });

  it("shows an Agent result before the Brain observes the settled batch", async () => {
    const completedEvent: ConversationEvent = {
      ...event,
      event_id: "event-completed",
      event_type: "agent.task_completed",
      payload: { agent_id: "hr-bot", agent_name: "HR Agent", status: "completed" },
    };
    const pageClient = client({
      streamEvents: vi.fn().mockImplementation(async (_id, options) => {
        options.onEvent(completedEvent);
      }),
    });
    await act(async () => root.render(
      <ConversationPage account={account} client={pageClient} conversationId={conversationId} />,
    ));
    const details = container.querySelector<HTMLDetailsElement>(".execution-card")!;
    details.open = true;

    expect(container.textContent).toContain("HR Agent 已完成");
    expect(container.textContent).toContain("等待 Agent 大脑继续处理");
    expect(container.textContent).not.toContain("Agent 大脑已读取结果");
  });

  it("lets the user answer a waiting-user request in the same turn", async () => {
    const waiting: ConversationTurn = {
      ...completedTurn,
      assistant_message_id: null,
      status: "waiting_user",
    };
    const stream = deferred<void>();
    const pageClient = client({
      fetchConversation: vi.fn().mockResolvedValue({ conversation, current_turn: waiting }),
      streamEvents: vi.fn().mockImplementation((_id, options) => {
        options.onEvent({
          ...event,
          event_type: "brain.user_input_requested",
          payload: { objective_summary: "请补充岗位级别", status: "waiting_user" },
        });
        return stream.promise;
      }),
    });
    await act(async () => root.render(
      <ConversationPage account={account} client={pageClient} conversationId={conversationId} />,
    ));

    expect(container.textContent).toContain("请补充岗位级别");
    const input = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='回答 Agent 大脑']");
    expect(input).not.toBeNull();
  });

  it("submits one rating for the selected assistant answer", async () => {
    const submitFeedback = vi.fn().mockResolvedValue({
      feedback_id: "feedback-1", conversation_id: conversationId,
      message_id: "message-2", turn_id: "turn-1",
      rating: "helpful", created_at: "2026-08-23T10:03:00Z",
    });
    const pageClient = client({ submitFeedback } as unknown as Partial<ConversationPageClient>);
    await act(async () => root.render(
      <ConversationPage account={account} client={pageClient} conversationId={conversationId} />,
    ));

    const helpful = container.querySelector<HTMLButtonElement>(
      "button[aria-label='这个回答有帮助']",
    );
    expect(helpful).not.toBeNull();
    await act(async () => helpful?.click());

    expect(submitFeedback).toHaveBeenCalledWith(
      "message-2", "helpful", account.csrf_token, expect.any(AbortSignal),
    );
    expect(container.textContent).toContain("已记录你的反馈");
    expect(container.querySelector<HTMLButtonElement>(
      "button[aria-label='这个回答没有帮助']",
    )?.disabled).toBe(true);
  });
});
