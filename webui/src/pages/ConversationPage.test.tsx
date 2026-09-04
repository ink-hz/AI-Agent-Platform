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
  ConversationInterventionResult,
  ConversationSubmissionResult,
  ConversationTaskDetail,
  ConversationTurn,
} from "../conversationTypes";
import { ConversationPage, type ConversationPageClient } from "./ConversationPage";


const conversationId = "8c13c965-1b60-472e-b275-199987d1d109";
const account: Account = {
  internal_user_id: "member", display_name: "洛奇", role: "member",
  departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
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
    input_attachments: [], output_attachments: [], active_attachment_ids: [],
  },
  {
    message_id: "message-2", conversation_id: conversationId, seq: 2, role: "assistant",
    content: "## 第一轮结果\n\n- 建议从 GitHub 开始", turn_id: "turn-1", delivery_status: "completed",
    created_at: "2026-08-23T10:01:00Z", completed_at: "2026-08-23T10:01:00Z",
    input_attachments: [], output_attachments: [], active_attachment_ids: [],
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
      input_attachments: [], output_attachments: [], active_attachment_ids: [],
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
    fetchTaskDetail: vi.fn().mockResolvedValue({
      task_id: "task-1", child_session_id: "child-1", agent_id: "hr-bot",
      status: "running", session_status: "active", messages: [], events: [],
    } satisfies ConversationTaskDetail),
    createMessageSubmission: vi.fn().mockImplementation((_id, text) => ({
      idempotencyKey: "same", send: vi.fn().mockResolvedValue(submissionResult(text)),
    })),
    streamEvents: vi.fn().mockImplementation(async (_id, options) => options.onEvent(event)),
    cancelCurrentTurn: vi.fn(),
    confirmAction: vi.fn(),
    rejectAction: vi.fn(),
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

  it("renders Markdown without member diagnostics and keeps the composer after a follow-up", async () => {
    const pageClient = client();
    const onConversationUpdated = vi.fn();
    await act(async () => root.render(<ConversationPage
      account={account} client={pageClient} conversationId={conversationId}
      onConversationUpdated={onConversationUpdated}
    />));

    expect(container.querySelector(".conversation-assistant h2")?.textContent).toBe("第一轮结果");
    expect(container.textContent).not.toContain("历史对话");
    expect(onConversationUpdated).toHaveBeenCalledWith(conversation);
    expect(container.querySelector(".execution-card")).toBeNull();
    expect(container.textContent).not.toContain("执行过程");
    expect(container.textContent).not.toContain("诊断详情");
    expect(container.querySelector("textarea[aria-label='继续对话']")).not.toBeNull();
    expect(container.querySelector<HTMLButtonElement>(".conversation-send")?.textContent).toBe("✨ 发送");

    await setTextarea(container, "继续给出搜索式");
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-send")?.click());

    expect(pageClient.createMessageSubmission).toHaveBeenCalledWith(conversationId, "继续给出搜索式", account.csrf_token);
    expect(onConversationUpdated).toHaveBeenLastCalledWith(expect.objectContaining({
      conversation_id: conversationId, updated_at: "2026-08-23T10:02:00Z",
    }));
    expect(container.textContent).toContain("继续给出搜索式");
    expect(container.querySelector("textarea[aria-label='继续对话']")).not.toBeNull();
  });

  it("sends a follow-up with Enter but keeps Shift+Enter and IME Enter for editing", async () => {
    const createMessageSubmission = vi.fn().mockImplementation((_id, text) => ({
      idempotencyKey: "same", send: vi.fn().mockResolvedValue(submissionResult(text)),
    }));
    const pageClient = client({ createMessageSubmission });
    await act(async () => root.render(<ConversationPage
      account={account} client={pageClient} conversationId={conversationId}
    />));
    await setTextarea(container, "继续补充岗位信息");
    const textarea = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='继续对话']")!;

    await act(async () => textarea.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter", shiftKey: true })));
    await act(async () => textarea.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter", isComposing: true })));
    expect(createMessageSubmission).not.toHaveBeenCalled();

    await act(async () => textarea.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Enter" })));

    expect(createMessageSubmission).toHaveBeenCalledWith(
      conversationId, "继续补充岗位信息", account.csrf_token,
    );
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
    expect(container.querySelector("textarea[aria-label='补充当前任务']")).not.toBeNull();
  });

  it("confirms only a real server-projected Action with the account CSRF token", async () => {
    const digest = "a".repeat(64);
    const confirmAction = vi.fn().mockResolvedValue({
      actionId: "action-1", taskId: "task-1", actionKind: "voc.submit_draft",
      status: "confirmed", executionStatus: "queued", summary: "提交本次 VOC 草稿",
      impact: "确认后会提交当前草稿。", actionDigest: digest,
      expiresAt: "2026-08-28T12:00:00Z", confirmedAt: "2026-08-28T10:01:00Z",
      confirmedBy: null,
    });
    const streamEvents = vi.fn().mockImplementation(async (_id, options) => {
      options.onEvent({
        event_id: "event-task", conversation_id: conversationId, seq: 1, turn_id: "turn-1",
        event_type: "agent.task_dispatched", created_at: "2026-08-28T10:00:00Z",
        payload: {
          task_id: "task-1", child_session_id: "child-1", agent_id: "voc",
          objective_summary: "整理 VOC 草稿", public_reason: "需要 VOC Agent",
          status: "running",
        },
      });
      options.onEvent({
        event_id: "event-action", conversation_id: conversationId, seq: 2, turn_id: "turn-1",
        event_type: "agent.action_required", created_at: "2026-08-28T10:00:01Z",
        payload: {
          task_id: "task-1", action_id: "action-1", action_kind: "voc.submit_draft",
          summary: "提交本次 VOC 草稿", impact: "确认后会提交当前草稿。",
          status: "pending", execution_status: "not_started", action_digest: digest,
          expires_at: "2026-08-28T12:00:00Z", confirmed_at: null, confirmed_by: null,
        },
      });
    });
    const pageClient = client({ confirmAction, streamEvents });
    await act(async () => root.render(<ConversationPage
      account={account}
      client={pageClient}
      conversationId={conversationId}
    />));

    expect(container.textContent).toContain("提交本次 VOC 草稿");
    const button = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((item) => item.textContent === "确认执行");
    await act(async () => button?.click());

    expect(confirmAction).toHaveBeenCalledWith(
      conversationId, "action-1", digest, account.csrf_token,
    );
  });

  it("resumes SSE from the last accepted sequence after reconnect", async () => {
    const active: ConversationTurn = { ...completedTurn, assistant_message_id: null, status: "running" };
    const fetchConversation = vi.fn()
      .mockResolvedValueOnce({ conversation, current_turn: active })
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

  it("recovers a completed professional-Agent turn when the SSE slot is temporarily unavailable", async () => {
    const directConversation = { ...conversation, mode: "direct_agent" as const, direct_agent_id: "hr-bot" };
    const active: ConversationTurn = { ...completedTurn, assistant_message_id: null, status: "running" };
    const fetchConversation = vi.fn()
      .mockResolvedValueOnce({ conversation: directConversation, current_turn: active })
      .mockResolvedValueOnce({ conversation: directConversation, current_turn: completedTurn });
    const fetchMessages = vi.fn()
      .mockResolvedValueOnce(messages.slice(0, 1))
      .mockResolvedValueOnce(messages);
    const streamEvents = vi.fn().mockRejectedValue(new Error("Conversation stream limit reached"));
    const reconnectDelay = vi.fn().mockResolvedValue(undefined);
    const pageClient = client({ fetchConversation, fetchMessages, streamEvents, reconnectDelay });

    await act(async () => root.render(<ConversationPage
      account={account}
      assistantLabel="HR Agent"
      client={pageClient}
      conversationId={conversationId}
      expectedAgentId="hr-bot"
    />));

    expect(streamEvents).toHaveBeenCalledTimes(1);
    expect(fetchConversation).toHaveBeenCalledTimes(2);
    expect(fetchMessages).toHaveBeenCalledTimes(2);
    expect(reconnectDelay).not.toHaveBeenCalled();
    expect(container.textContent).toContain("建议从 GitHub 开始");
    expect(container.textContent).not.toContain("连接暂时中断");
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

  it("keeps an archived conversation readable but disables every write path", async () => {
    const archivedConversation: Conversation = {
      ...conversation, status: "archived", archived_at: "2026-08-24T10:00:00Z",
    };
    const pageClient = client({
      fetchConversation: vi.fn().mockResolvedValue({ conversation: archivedConversation, current_turn: completedTurn }),
    });
    await act(async () => root.render(<ConversationPage
      account={account} client={pageClient} conversationId={conversationId}
    />));

    expect(container.textContent).toContain("建议从 GitHub 开始");
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>(".conversation-send")?.disabled).toBe(true);
    expect(container.textContent).toContain("当前为只读状态");
    expect(container.querySelector("button[aria-label='这个回答有帮助']")).toBeNull();
  });

  it("labels a direct-Agent answer as the selected professional Agent", async () => {
    const directConversation = { ...conversation, mode: "direct_agent" as const, direct_agent_id: "hr-bot" };
    const pageClient = client({
      fetchConversation: vi.fn().mockResolvedValue({ conversation: directConversation, current_turn: completedTurn }),
    });
    await act(async () => root.render(<ConversationPage
      account={account}
      assistantLabel="HR Agent"
      client={pageClient}
      conversationId={conversationId}
      personaSubtitle="Hannah · 技术人才搜寻与招聘协作"
    />));

    expect(container.querySelector(".conversation-assistant header strong")?.textContent).toBe("HR Agent");
    expect(container.querySelector(".conversation-header h1")?.textContent).toBe("HR Agent");
    expect(container.textContent).toContain("Hannah · 技术人才搜寻与招聘协作");
    expect(container.querySelector(".multi-agent-workroom")).toBeNull();
  });

  it("supports focused thread supplements and hides the permanent materials column", async () => {
    const limits = {
      max_file_bytes: 50 * 1024 * 1024,
      max_files_per_message: 5,
      max_bytes_per_message: 50 * 1024 * 1024,
      max_files_per_conversation: 50,
      max_bytes_per_conversation: 500 * 1024 * 1024,
    };
    await act(async () => root.render(<ConversationPage
      account={account}
      attachmentLimits={limits}
      client={client({ listAttachments: vi.fn().mockResolvedValue([]) })}
      composerTools={<button type="button">岗位任务</button>}
      conversationId={conversationId}
      materialsPresentation="hidden"
      threadSupplement={<section aria-label="岗位任务进度">执行中</section>}
    />));

    const messagesNode = container.querySelector(".conversation-messages")!;
    const supplement = container.querySelector('[aria-label="岗位任务进度"]')!;
    const composer = container.querySelector(".conversation-composer")!;
    expect(messagesNode.compareDocumentPosition(supplement) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(supplement.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(composer.textContent).toContain("岗位任务");
    expect(container.querySelector(".session-materials-drawer")).toBeNull();
    expect(container.querySelector(".conversation-workspace-grid")).toBeNull();
  });

  it("keeps a direct Agent composer locked while its current Turn is active", async () => {
    const directConversation = { ...conversation, mode: "direct_agent" as const, direct_agent_id: "hr-bot" };
    const active: ConversationTurn = { ...completedTurn, assistant_message_id: null, status: "running" };
    const stream = deferred<void>();
    await act(async () => root.render(<ConversationPage
      account={account}
      assistantLabel="HR Agent"
      client={client({
        fetchConversation: vi.fn().mockResolvedValue({ conversation: directConversation, current_turn: active }),
        streamEvents: vi.fn().mockReturnValue(stream.promise),
      })}
      conversationId={conversationId}
    />));

    expect(container.querySelector<HTMLTextAreaElement>("textarea[aria-label='继续对话']")?.disabled).toBe(true);
  });

  it("shows progress only for the current direct Agent Turn", async () => {
    const directConversation = { ...conversation, mode: "direct_agent" as const, direct_agent_id: "hr-bot" };
    const active: ConversationTurn = { ...completedTurn, turn_id: "turn-2", assistant_message_id: null, status: "running" };
    const stream = deferred<void>();
    const streamEvents = vi.fn().mockImplementation((_id, options) => {
      options.onEvent({
        ...event, event_id: "old-progress", turn_id: "turn-1", seq: 1,
        event_type: "agent.task_progress", payload: { summary: "上一轮进度" },
      });
      options.onEvent({
        ...event, event_id: "current-progress", turn_id: "turn-2", seq: 2,
        event_type: "agent.task_progress", payload: { summary: "正在处理这一轮" },
      });
      return stream.promise;
    });
    await act(async () => root.render(<ConversationPage
      account={account}
      assistantLabel="HR Agent"
      client={client({
        fetchConversation: vi.fn().mockResolvedValue({ conversation: directConversation, current_turn: active }),
        streamEvents,
      })}
      conversationId={conversationId}
    />));

    expect(container.textContent).toContain("正在处理这一轮");
    expect(container.textContent).not.toContain("上一轮进度");
  });

  it("embeds a real Agent workroom in its Turn before the final answer", async () => {
    const dispatchedEvent: ConversationEvent = {
      ...event, event_id: "event-dispatched", event_type: "agent.task_dispatched",
      payload: {
        task_id: "task-1", child_session_id: "child-1", agent_id: "hr-bot",
        objective_summary: "定位人才", public_reason: "需要人才判断", status: "running",
      },
    };
    const pageClient = client({
      streamEvents: vi.fn().mockImplementation(async (_id, options) => {
        options.onEvent(dispatchedEvent);
      }),
    });
    await act(async () => root.render(
      <ConversationPage account={account} client={pageClient} conversationId={conversationId} />,
    ));
    const userMessage = container.querySelector(".conversation-user");
    const workroom = container.querySelector(".multi-agent-workroom");
    const answer = container.querySelector(".conversation-assistant");
    expect(workroom?.textContent).toContain("HR Agent");
    expect(userMessage!.compareDocumentPosition(workroom!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(workroom!.compareDocumentPosition(answer!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.textContent).not.toContain("查看协作过程");
  });

  it("keeps the Brain composer enabled and sends an intervention into the active Turn", async () => {
    const active: ConversationTurn = { ...completedTurn, assistant_message_id: null, status: "waiting_agents" };
    const intervention: ConversationInterventionResult = {
      intervention: { status: "pending", message_id: "message-3" },
      message: {
        message_id: "message-3", conversation_id: conversationId, seq: 3, role: "user",
        content: "把范围改成深圳", turn_id: "turn-1", delivery_status: "accepted",
        created_at: "2026-08-25T10:02:00Z", completed_at: null,
        input_attachments: [], output_attachments: [], active_attachment_ids: [],
      },
      turn: active,
    };
    const createMessageSubmission = vi.fn().mockReturnValue({
      idempotencyKey: "intervention", send: vi.fn().mockResolvedValue(intervention),
    });
    const stream = deferred<void>();
    const pageClient = client({
      fetchConversation: vi.fn().mockResolvedValue({ conversation, current_turn: active }),
      createMessageSubmission,
      streamEvents: vi.fn().mockReturnValue(stream.promise),
    });
    await act(async () => root.render(
      <ConversationPage account={account} client={pageClient} conversationId={conversationId} />,
    ));

    const composer = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='补充当前任务']");
    expect(composer?.disabled).toBe(false);
    await setTextarea(container, "把范围改成深圳");
    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-send")?.click());

    expect(createMessageSubmission).toHaveBeenCalledWith(conversationId, "把范围改成深圳", "csrf");
    expect(container.textContent).toContain("把范围改成深圳");
    expect(container.querySelector<HTMLTextAreaElement>("textarea[aria-label='补充当前任务']")?.disabled).toBe(false);
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
      rating: "helpful", reason: null, created_at: "2026-08-23T10:03:00Z",
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
      "message-2", "helpful", null, null, account.csrf_token, expect.any(AbortSignal),
    );
    expect(container.textContent).toContain("已记录你的反馈");
    expect(container.querySelector<HTMLButtonElement>(
      "button[aria-label='这个回答需改进']",
    )?.disabled).toBe(true);
  });

  it("collects an improvement reason and optional comment before submitting", async () => {
    const submitFeedback = vi.fn().mockResolvedValue({
      feedback_id: "feedback-2", conversation_id: conversationId,
      message_id: "message-2", turn_id: "turn-1", rating: "unhelpful",
      reason: "incomplete", created_at: "2026-08-23T10:03:00Z",
    });
    await act(async () => root.render(<ConversationPage
      account={account} client={client({ submitFeedback })} conversationId={conversationId}
    />));

    await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='这个回答需改进']")?.click());
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>(".conversation-feedback-detail button")]
      .find((button) => button.textContent === "信息不完整")?.click());
    const comment = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='补充改进建议']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(comment, "缺少目标公司");
      comment.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>(".conversation-feedback-detail button")]
      .find((button) => button.textContent === "提交反馈")?.click());

    expect(submitFeedback).toHaveBeenCalledWith(
      "message-2", "unhelpful", "incomplete", "缺少目标公司", account.csrf_token,
      expect.any(AbortSignal),
    );
  });
});
