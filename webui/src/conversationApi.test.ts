/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendConversationMessage,
  archiveConversation,
  cancelCurrentTurn,
  confirmConversationAction,
  createConversationMessageSubmission,
  fetchConversation,
  fetchConversationMessages,
  fetchConversationTaskDetail,
  listConversationActions,
  listConversations,
  markConversationRead,
  renameConversation,
  rejectConversationAction,
  restoreConversation,
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
  delivery_status: "accepted",
  created_at: "2026-08-23T10:00:00Z",
  completed_at: null,
  input_attachments: [],
  output_attachments: [],
  active_attachment_ids: [],
};

const turn = {
  turn_id: TURN_ID,
  conversation_id: CONVERSATION_ID,
  user_message_id: MESSAGE_ID,
  assistant_message_id: null,
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
  it("persists the last visible terminal event for unread state", async () => {
    const payload = {
      conversation_id: CONVERSATION_ID,
      last_read_message_seq: 7,
      last_read_at: "2026-09-03T12:00:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(markConversationRead(CONVERSATION_ID, 7, "csrf")).resolves.toEqual({
      conversationId: CONVERSATION_ID,
      lastReadMessageSeq: 7,
      lastReadAt: payload.last_read_at,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/conversations/${CONVERSATION_ID}/read-state`,
      expect.objectContaining({
        method: "POST", body: JSON.stringify({ last_seen_event_seq: 7 }),
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
      }),
    );
  });

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
        body: JSON.stringify({ text: "继续", attachment_ids: [], active_attachment_ids: [] }),
        headers: { "Idempotency-Key": "58df615d-dfd1-4b02-87f7-9a1d7a04f7fa" },
      });
    }
  });

  it("serializes attachment selection and keeps it stable across retries", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("58df615d-dfd1-4b02-87f7-9a1d7a04f7fa");
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse(submissionResult, 201));
    vi.stubGlobal("fetch", fetchMock);
    const input = {
      text: "评估候选人",
      attachmentIds: ["attachment-new"],
      activeAttachmentIds: ["attachment-old", "attachment-new"],
    };
    const submission = createConversationMessageSubmission(CONVERSATION_ID, input, "csrf");

    await expect(submission.send()).rejects.toThrow("offline");
    input.attachmentIds.push("mutated-after-creation");
    await expect(submission.send()).resolves.toEqual(submissionResult);

    for (const [, init] of fetchMock.mock.calls) {
      expect(init.body).toBe(JSON.stringify({
        text: "评估候选人",
        attachment_ids: ["attachment-new"],
        active_attachment_ids: ["attachment-old", "attachment-new"],
      }));
      expect(init.headers["Idempotency-Key"]).toBe("58df615d-dfd1-4b02-87f7-9a1d7a04f7fa");
    }
  });

  it("parses strict attachment and recovery projections without internal fields", async () => {
    const attachment = {
      attachment_id: "attachment-1",
      conversation_id: CONVERSATION_ID,
      source: "user",
      display_name: "candidate.pdf",
      detected_mime: "application/pdf",
      size_bytes: 4096,
      state: "ready",
      created_at: "2026-09-03T10:00:00Z",
      retained_until: "2027-09-03T10:00:00Z",
      processing_coverage: { coverage: "first_page", download: true, inline_preview: true },
      availability_reason: null,
    };
    const recovered = {
      ...message,
      role: "assistant",
      delivery_status: "completed",
      input_attachments: [attachment],
      active_attachment_ids: ["attachment-1"],
      search_recovery: {
        status: "partial",
        attempt_count: 2,
        last_attempt_at: "2026-09-03T10:01:00Z",
        resumable: true,
        coverage_note: "部分外部来源暂不可用",
      },
      citations: [{
        citation_key: "source-1", title: "公开招聘页", url: "https://example.com/jobs",
        site: "example.com", retrieved_at: "2026-09-03T10:01:00Z", supports: ["研发岗位"],
      }],
      artifact_versions: [{
        artifact_key: "candidate-report", version_no: 1, producer_version_id: "report-v1",
        current: true, status: "ready", attachment: { ...attachment, source: "agent" },
      }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ items: [recovered] })));

    await expect(fetchConversationMessages(CONVERSATION_ID)).resolves.toEqual([
      expect.objectContaining({
        input_attachments: [expect.objectContaining({
          attachmentId: "attachment-1",
          displayName: "candidate.pdf",
          preview: { attachmentId: "attachment-1", detectedMime: "application/pdf" },
        })],
        search_recovery: {
          status: "partial",
          attemptCount: 2,
          lastAttemptAt: "2026-09-03T10:01:00Z",
          resumable: true,
          coverageNote: "部分外部来源暂不可用",
        },
        citations: [expect.objectContaining({ citationKey: "source-1" })],
        artifact_versions: [expect.objectContaining({
          artifactKey: "candidate-report", current: true,
          attachment: expect.objectContaining({ source: "agent" }),
        })],
      }),
    ]);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      items: [{ ...recovered, input_attachments: [{ ...attachment, object_ref: "private" }] }],
    })));
    await expect(fetchConversationMessages(CONVERSATION_ID))
      .rejects.toThrow("Attachment response invalid");
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

  it("accepts a 202 intervention response without inventing a second Turn", async () => {
    const interventionMessage = {
      ...message, message_id: "message-intervention", seq: 2,
      content: "把范围改成深圳", turn_id: TURN_ID,
    };
    const intervention = {
      intervention: { status: "pending", message_id: "message-intervention" },
      message: interventionMessage,
      turn: { ...turn, status: "waiting_agents" },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(intervention, 202)));

    await expect(createConversationMessageSubmission(
      CONVERSATION_ID, "把范围改成深圳", "csrf",
    ).send()).resolves.toEqual(intervention);
  });

  it("loads a strict owner-scoped child task transcript", async () => {
    const task = {
      task_id: "task-1", child_session_id: "child-1", agent_id: "hr-bot",
      status: "running", session_status: "active",
      messages: [{
        seq: 1, sender: "agent", kind: "message", text: "已定位候选人",
        created_at: "2026-08-26T02:00:00Z",
      }],
      events: [{
        seq: 1, kind: "work", source: "agent_sdk", source_ref: "run:1",
        summary: "检索 GitHub", status: "running", evidence_refs: [], artifact_refs: [],
        created_at: "2026-08-26T02:00:00Z",
      }],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(task));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchConversationTaskDetail(CONVERSATION_ID, TURN_ID, "task-1"))
      .resolves.toEqual(task);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/conversations/${CONVERSATION_ID}/turns/${TURN_ID}/tasks/task-1`,
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("lists and mutates only strict server-projected Actions", async () => {
    const action = {
      action_id: "f6a459d8-b081-58dd-908f-083976d0b481",
      task_id: "074557ca-58a5-4555-b5a2-5793ef30a298",
      action_kind: "voc.submit_draft",
      summary: "提交本次 VOC 草稿",
      impact: "确认后会提交当前草稿。",
      status: "pending",
      execution_status: "not_started",
      action_digest: "a".repeat(64),
      action_digest_prefix: "a".repeat(12),
      expires_at: "2026-08-28T12:00:00+00:00",
      confirmed_at: null,
      execution_deadline_at: null,
    };
    const confirmed = {
      ...action,
      status: "confirmed",
      execution_status: "queued",
      confirmed_at: "2026-08-28T10:01:00+00:00",
      execution_deadline_at: "2026-08-28T10:06:00+00:00",
    };
    const rejected = { ...action, status: "rejected" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [action] }))
      .mockResolvedValueOnce(jsonResponse(confirmed))
      .mockResolvedValueOnce(jsonResponse(rejected));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listConversationActions(CONVERSATION_ID)).resolves.toEqual([
      expect.objectContaining({
        actionId: action.action_id,
        actionKind: "voc.submit_draft",
        summary: "提交本次 VOC 草稿",
        status: "pending",
        confirmedBy: null,
      }),
    ]);
    await expect(confirmConversationAction(
      CONVERSATION_ID, action.action_id, action.action_digest, "csrf",
    )).resolves.toEqual(expect.objectContaining({ status: "confirmed", executionStatus: "queued" }));
    await expect(rejectConversationAction(
      CONVERSATION_ID, action.action_id, "csrf",
    )).resolves.toEqual(expect.objectContaining({ status: "rejected" }));

    expect(fetchMock.mock.calls[0]).toEqual([
      `/api/v1/conversations/${CONVERSATION_ID}/actions`,
      expect.objectContaining({ credentials: "include" }),
    ]);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
      body: JSON.stringify({ action_digest: action.action_digest }),
    });
    expect(fetchMock.mock.calls[2][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
    });
  });

  it("rejects Action projections containing parameters or internal owner identifiers", async () => {
    const unsafe = {
      action_id: "f6a459d8-b081-58dd-908f-083976d0b481",
      task_id: "074557ca-58a5-4555-b5a2-5793ef30a298",
      action_kind: "voc.submit_draft",
      summary: "提交草稿",
      impact: "提交",
      status: "pending",
      execution_status: "not_started",
      action_digest: "a".repeat(64),
      action_digest_prefix: "a".repeat(12),
      expires_at: "2026-08-28T12:00:00+00:00",
      confirmed_at: null,
      execution_deadline_at: null,
      parameters: { raw: true },
      confirmed_by_internal_user_id: "29cf0e76-e572-4ce0-8e24-f8eb69b8620a",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ items: [unsafe] })));

    await expect(listConversationActions(CONVERSATION_ID))
      .rejects.toThrow("Action response invalid");
  });

  it("accepts only the member-safe message shape without mission identifiers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ items: [message] })));

    await expect(fetchConversationMessages(CONVERSATION_ID)).resolves.toEqual([message]);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      items: [{ ...message, mission_id: MISSION_ID }],
    })));
    await expect(fetchConversationMessages(CONVERSATION_ID)).rejects.toThrow("Message response invalid");
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
      .mockResolvedValueOnce(jsonResponse({ conversation_id: CONVERSATION_ID, turn_id: TURN_ID, cancel_requested: true }))
      .mockResolvedValueOnce(jsonResponse({ ...conversation, status: "archived", archived_at: "2026-08-23T11:00:00Z" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchConversation(CONVERSATION_ID)).resolves.toEqual({ conversation, current_turn: turn });
    await expect(listConversations(undefined, "next/opaque", 10)).resolves.toEqual({ items: [conversation], next_cursor: "next" });
    await expect(fetchConversationMessages(CONVERSATION_ID)).resolves.toEqual([message]);
    await expect(cancelCurrentTurn(CONVERSATION_ID, "csrf")).resolves.toEqual({
      conversation_id: CONVERSATION_ID, turn_id: TURN_ID, cancel_requested: true,
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

  it("renames, lists archived history, and restores with exact member endpoints", async () => {
    const archived = { ...conversation, status: "archived", archived_at: "2026-08-25T10:00:00Z" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ...conversation, title: "新标题" }))
      .mockResolvedValueOnce(jsonResponse({ items: [archived], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse(conversation));
    vi.stubGlobal("fetch", fetchMock);

    await renameConversation(CONVERSATION_ID, "新标题", "csrf");
    await listConversations(undefined, undefined, 20, undefined, "archived");
    await restoreConversation(CONVERSATION_ID, "csrf");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `/api/v1/conversations/${CONVERSATION_ID}`,
      "/api/v1/conversations?limit=20&status=archived",
      `/api/v1/conversations/${CONVERSATION_ID}/restore`,
    ]);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "PATCH", body: JSON.stringify({ title: "新标题" }),
    });
  });

  it("submits strict per-assistant-message feedback", async () => {
    const feedback = {
      feedback_id: "feedback-1",
      conversation_id: CONVERSATION_ID,
      message_id: MESSAGE_ID,
      turn_id: TURN_ID,
      rating: "helpful",
      reason: null,
      created_at: "2026-08-23T10:03:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(feedback, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(submitConversationFeedback(MESSAGE_ID, "helpful", null, null, "csrf"))
      .resolves.toEqual(feedback);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/messages/${MESSAGE_ID}/feedback`,
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf" }),
        body: JSON.stringify({ rating: "helpful", reason: null, comment: null }),
      }),
    );

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ ...feedback, content: "must reject" })));
    await expect(submitConversationFeedback(MESSAGE_ID, "helpful", null, null, "csrf"))
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
      turn_id: TURN_ID, event_type: "turn.running",
      payload: { status: "running" }, created_at: "2026-08-23T10:00:01Z",
    });
    const second = JSON.stringify({
      event_id: "event-2", conversation_id: CONVERSATION_ID, seq: 3,
      turn_id: TURN_ID, event_type: "message.completed",
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
    `id: 2\nevent: conversation\ndata: ${JSON.stringify({ event_id: "e", conversation_id: CONVERSATION_ID, seq: 1, turn_id: null, event_type: "x", payload: {}, created_at: "now" })}\n\n`,
    `id: 2\nevent: conversation\ndata: ${JSON.stringify({ event_id: "e", conversation_id: "another", seq: 2, turn_id: null, event_type: "x", payload: {}, created_at: "now" })}\n\n`,
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
