/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendConversationMessage,
  archiveConversation,
  cancelCurrentTurn,
  createConversationMessageSubmission,
  fetchConversation,
  fetchConversationMessages,
  listConversations,
  startConversation,
  streamConversationEvents,
  submitConversationFeedback,
} from "./conversationApi";


const CONVERSATION_ID = "8c13c965-1b60-472e-b275-199987d1d109";
const MESSAGE_ID = "4e2ac19d-00cc-43ca-a953-f678b8bf7029";
const TURN_ID = "adac44bf-cb88-4d60-bc23-492cd5fbb69f";
const MISSION_ID = "8077f668-5057-465f-9984-d73e68af6393";

const conversation = {
  conversation_id: CONVERSATION_ID,
  mode: "brain",
  direct_agent_id: null,
  title: "定义候选人画像",
  status: "active",
  summary_through_seq: 0,
  created_at: "2026-08-23T10:00:00Z",
  updated_at: "2026-08-23T10:00:00Z",
  archived_at: null,
};

const message = {
  message_id: MESSAGE_ID,
  conversation_id: CONVERSATION_ID,
  seq: 1,
  role: "user",
  content: "定义候选人画像",
  turn_id: TURN_ID,
  mission_id: MISSION_ID,
  delivery_status: "accepted",
  created_at: "2026-08-23T10:00:00Z",
  completed_at: null,
};

const turn = {
  turn_id: TURN_ID,
  conversation_id: CONVERSATION_ID,
  user_message_id: MESSAGE_ID,
  assistant_message_id: null,
  mission_id: MISSION_ID,
  retry_of_turn_id: null,
  status: "accepted",
  created_at: "2026-08-23T10:00:00Z",
  updated_at: "2026-08-23T10:00:00Z",
};

const submissionResult = { conversation, message, turn };


function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}


afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});


describe("continuous Conversation API", () => {
  it("starts brain and direct-Agent conversations using the exact endpoints", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("58df615d-dfd1-4b02-87f7-9a1d7a04f7fa");
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(submissionResult, 201)));
    vi.stubGlobal("fetch", fetchMock);

    await startConversation("定义候选人画像", "csrf").send();
    await startConversation("评估这份简历", "csrf", "hr-bot").send();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/conversations");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/agents/hr-bot/conversations");
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toMatchObject({
        method: "POST",
        credentials: "include",
        body: expect.any(String),
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": "csrf",
          "Idempotency-Key": "58df615d-dfd1-4b02-87f7-9a1d7a04f7fa",
        },
      });
    }
  });

  it("reuses one conversation for a follow-up and retains its UUID across retries", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("58df615d-dfd1-4b02-87f7-9a1d7a04f7fa");
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse(submissionResult, 201));
    vi.stubGlobal("fetch", fetchMock);
    const submission = createConversationMessageSubmission(CONVERSATION_ID, "继续", "csrf");

    await expect(submission.send()).rejects.toThrow("offline");
    await expect(submission.send()).resolves.toEqual(submissionResult);

    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toBe(`/api/v1/conversations/${CONVERSATION_ID}/messages`);
      expect(init).toMatchObject({
        method: "POST",
        body: JSON.stringify({ text: "继续" }),
        headers: { "Idempotency-Key": "58df615d-dfd1-4b02-87f7-9a1d7a04f7fa" },
      });
    }
  });

  it("provides the direct append convenience function", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(submissionResult, 201));
    vi.stubGlobal("fetch", fetchMock);

    await appendConversationMessage(CONVERSATION_ID, "继续", "csrf");

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/conversations/${CONVERSATION_ID}/messages`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("rejects empty or over-32-KiB input before writing", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    expect(() => startConversation("   ", "csrf")).toThrow("required");
    expect(() => createConversationMessageSubmission(CONVERSATION_ID, "人".repeat(11_000), "csrf"))
      .toThrow("32 KiB");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reads detail, history, messages, cancel, and archive with credentials", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ conversation, current_turn: turn }))
      .mockResolvedValueOnce(jsonResponse({ items: [conversation], next_cursor: "next" }))
      .mockResolvedValueOnce(jsonResponse({ items: [message] }))
      .mockResolvedValueOnce(jsonResponse({ conversation_id: CONVERSATION_ID, turn_id: TURN_ID, mission_id: MISSION_ID, cancel_requested: true }))
      .mockResolvedValueOnce(jsonResponse({ ...conversation, status: "archived", archived_at: "2026-08-23T11:00:00Z" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchConversation(CONVERSATION_ID)).resolves.toEqual({ conversation, current_turn: turn });
    await expect(listConversations(undefined, "next/opaque", 10)).resolves.toEqual({ items: [conversation], next_cursor: "next" });
    await expect(fetchConversationMessages(CONVERSATION_ID)).resolves.toEqual([message]);
    await expect(cancelCurrentTurn(CONVERSATION_ID, "csrf")).resolves.toEqual({
      conversation_id: CONVERSATION_ID, turn_id: TURN_ID, mission_id: MISSION_ID, cancel_requested: true,
    });
    await expect(archiveConversation(CONVERSATION_ID, "csrf")).resolves.toMatchObject({ status: "archived" });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `/api/v1/conversations/${CONVERSATION_ID}`,
      "/api/v1/conversations?limit=10&before=next%2Fopaque",
      `/api/v1/conversations/${CONVERSATION_ID}/messages`,
      `/api/v1/conversations/${CONVERSATION_ID}/turns/current/cancel`,
      `/api/v1/conversations/${CONVERSATION_ID}/archive`,
    ]);
    expect(fetchMock.mock.calls[3][1]).toMatchObject({ method: "POST", headers: { "X-CSRF-Token": "csrf" } });
    expect(fetchMock.mock.calls[4][1]).toMatchObject({ method: "POST", headers: { "X-CSRF-Token": "csrf" } });
  });

  it("scopes professional history to the selected immutable Agent", async () => {
    const direct = { ...conversation, mode: "direct_agent", direct_agent_id: "marketing-gtm-bot" };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [direct], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listConversations(undefined, undefined, 20, "marketing-gtm-bot"))
      .resolves.toEqual({ items: [direct], next_cursor: null });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/conversations?limit=20&direct_agent_id=marketing-gtm-bot",
    );
  });

  it("submits strict per-assistant-message feedback", async () => {
    const feedback = {
      feedback_id: "feedback-1",
      conversation_id: CONVERSATION_ID,
      message_id: MESSAGE_ID,
      turn_id: TURN_ID,
      mission_id: MISSION_ID,
      rating: "helpful",
      created_at: "2026-08-23T10:03:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(feedback, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(submitConversationFeedback(MESSAGE_ID, "helpful", "csrf"))
      .resolves.toEqual(feedback);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/messages/${MESSAGE_ID}/feedback`,
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
        body: JSON.stringify({ rating: "helpful" }),
      }),
    );

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ ...feedback, content: "must reject" })));
    await expect(submitConversationFeedback(MESSAGE_ID, "helpful", "csrf"))
      .rejects.toThrow("feedback response invalid");
  });

  it.each([
    { ...conversation, unexpected: true },
    { ...conversation, status: "deleted" },
    { ...conversation, summary_through_seq: -1 },
  ])("rejects malformed or extra conversation response fields", async (invalid) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ conversation: invalid, current_turn: null })));
    await expect(fetchConversation(CONVERSATION_ID)).rejects.toThrow("Conversation response invalid");
  });

  it("rejects extra top-level and nested response fields", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ conversation, current_turn: turn, extra: true }))
      .mockResolvedValueOnce(jsonResponse({ conversation, message: { ...message, extra: true }, turn }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchConversation(CONVERSATION_ID)).rejects.toThrow("detail response invalid");
    await expect(startConversation("开始", "csrf").send()).rejects.toThrow("Message response invalid");
  });

  it("accepts monotonic same-conversation SSE events split across chunks", async () => {
    const first = JSON.stringify({
      event_id: "event-1", conversation_id: CONVERSATION_ID, seq: 2,
      turn_id: TURN_ID, mission_id: MISSION_ID, event_type: "turn.running",
      payload: { status: "running" }, created_at: "2026-08-23T10:00:01Z",
    });
    const second = JSON.stringify({
      event_id: "event-2", conversation_id: CONVERSATION_ID, seq: 3,
      turn_id: TURN_ID, mission_id: MISSION_ID, event_type: "message.completed",
      payload: { message_id: MESSAGE_ID }, created_at: "2026-08-23T10:00:02Z",
    });
    const encoded = new TextEncoder().encode(
      `: heartbeat\n\nid: 2\nevent: conversation\ndata: ${first}\n\nid: 3\nevent: conversation\ndata: ${second}\n\n`,
    );
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded.slice(0, 29));
        controller.enqueue(encoded.slice(29));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream, {
      headers: { "Content-Type": "text/event-stream" },
    })));
    const events: unknown[] = [];

    await streamConversationEvents(CONVERSATION_ID, {
      after: 1,
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    });

    expect(events).toEqual([
      expect.objectContaining({ seq: 2, event_type: "turn.running" }),
      expect.objectContaining({ seq: 3, event_type: "message.completed" }),
    ]);
  });

  it.each([
    `id: 2\nevent: conversation\ndata: ${JSON.stringify({ event_id: "e", conversation_id: CONVERSATION_ID, seq: 1, turn_id: null, mission_id: null, event_type: "x", payload: {}, created_at: "now" })}\n\n`,
    `id: 2\nevent: conversation\ndata: ${JSON.stringify({ event_id: "e", conversation_id: "another", seq: 2, turn_id: null, mission_id: null, event_type: "x", payload: {}, created_at: "now" })}\n\n`,
    `id: 2\nevent: conversation\ndata: {"event_id":`,
  ])("rejects non-monotonic, cross-conversation, or truncated SSE frames", async (body) => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream, {
      headers: { "Content-Type": "text/event-stream" },
    })));

    await expect(streamConversationEvents(CONVERSATION_ID, {
      after: 1,
      signal: new AbortController().signal,
      onEvent: () => undefined,
    })).rejects.toThrow();
  });
});
