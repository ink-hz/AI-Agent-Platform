/** @vitest-environment jsdom */

import { afterEach, expect, it, vi } from "vitest";

import { createHrApi } from "./hrApi";


const POSITION_ID = "11111111-1111-4111-8111-111111111111";
const DRAFT_ID = "22222222-2222-4222-8222-222222222222";
const CONVERSATION_ID = "33333333-3333-4333-8333-333333333333";
const ATTACHMENT_ID = "44444444-4444-4444-8444-444444444444";
const REQUEST_ID = "55555555-5555-4555-8555-555555555555";
const NOW = "2026-09-04T10:00:00+08:00";

const position = {
  position_id: POSITION_ID, source_kind: "official_site", official_job_id: "J11014",
  title: "算法工程师", department: "机器人", locations: ["深圳"],
  official_status: "active", internal_status: "active", source_version: "sync-v1",
  row_version: 2, created_at: NOW, updated_at: NOW,
};
const positionDetail = {
  ...position, conversation_count: 1, material_count: 0, artifact_count: 0,
  conversation_ids: [CONVERSATION_ID], material_attachment_ids: [], artifact_ids: [],
  artifact_attachment_ids: [],
};
const draft = {
  draft_id: DRAFT_ID, source_kind: "new_conversation", source_key: "conversation:new",
  source_conversation_id: null, title: "结构工程师", proposal: {},
  evidence: { message_seq: 1 }, discovery_rule_version: "interactive-v1",
  state: "proposed", resolved_position_id: null, row_version: 1,
  created_at: NOW, updated_at: NOW,
};

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });


it("encodes position filters, credentials, and AbortSignal", async () => {
  const signal = new AbortController().signal;
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    items: [position], next_cursor: "next/page",
  }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const page = await createHrApi("csrf").listPositions({
    query: "光学 / 算法", source: "official_site", internalStatus: "active",
    cursor: "cursor+1", limit: 40,
  }, signal);

  expect(page.items[0].officialJobId).toBe("J11014");
  expect(String(fetchMock.mock.calls[0][0])).toBe(
    "/api/hr/positions?query=%E5%85%89%E5%AD%A6+%2F+%E7%AE%97%E6%B3%95&source=official_site&internal_status=active&cursor=cursor%2B1&limit=40",
  );
  expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "same-origin", signal });
});

it("accepts fallback JOBAD identifiers returned by the official job registry", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    items: [{ ...position, official_job_id: "JOBAD:113485" }], next_cursor: null,
  }), { status: 200 })));

  await expect(createHrApi("csrf").listPositions({})).resolves.toMatchObject({
    items: [{ officialJobId: "JOBAD:113485" }], nextCursor: null,
  });
});

it("parses position scope identifiers without crossing resource boundaries", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify(positionDetail), { status: 200 },
  )));

  await expect(createHrApi("csrf").position(POSITION_ID)).resolves.toMatchObject({
    positionId: POSITION_ID, conversationIds: [CONVERSATION_ID],
    materialAttachmentIds: [], artifactIds: [], artifactAttachmentIds: [],
  });
});


it("rejects malformed UUIDs, enums, timestamps, and unexpected response fields", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    items: [{ ...position, official_status: "unknown", external_ats: "beisen" }],
    next_cursor: null,
  }), { status: 200 })));

  await expect(createHrApi("csrf").listPositions({})).rejects.toThrow(
    "HR position response invalid",
  );
});


it("rejects malformed draft list envelopes before reading items", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    items: [draft], next_cursor: null,
  }), { status: 200 })));

  await expect(createHrApi("csrf").listDrafts("proposed")).rejects.toThrow(
    "HR position draft response invalid",
  );
});


it("sends CSRF and replay-stable request IDs for every mutation", async () => {
  const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
    const path = String(input);
    const body = path.includes("/conversations/")
      ? { position_id: POSITION_ID, conversation_id: CONVERSATION_ID,
          binding_kind: "created_in_position", previous_position_id: null, created_at: NOW }
      : path.includes("/materials/")
        ? { position_id: POSITION_ID, attachment_id: ATTACHMENT_ID,
            active: !path.includes("delete"), created_at: NOW, updated_at: NOW }
        : path.endsWith("/confirm") ? position : draft;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  const api = createHrApi("csrf-current");

  await api.proposeDraft({
    sourceKind: "new_conversation", sourceKey: "conversation:new",
    sourceConversationId: null, title: "结构工程师", proposal: {},
    evidence: { message_seq: 1 }, discoveryRuleVersion: "interactive-v1",
  }, REQUEST_ID);
  await api.confirmDraft(DRAFT_ID, 1, REQUEST_ID);
  await api.mergeDraft(DRAFT_ID, POSITION_ID, 1, REQUEST_ID);
  await api.dismissDraft(DRAFT_ID, 1, REQUEST_ID);
  await api.bindConversation(POSITION_ID, CONVERSATION_ID, REQUEST_ID);
  await api.promoteMaterial(POSITION_ID, ATTACHMENT_ID, REQUEST_ID);
  await api.removeMaterial(POSITION_ID, ATTACHMENT_ID, REQUEST_ID);

  for (const [, init] of fetchMock.mock.calls) {
    const headers = new Headers(init?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-current");
    expect(headers.get("Idempotency-Key")).toBe(REQUEST_ID);
    expect(init?.credentials).toBe("same-origin");
  }
  expect(fetchMock.mock.calls[fetchMock.mock.calls.length - 1]?.[1]?.method).toBe("DELETE");
});


it("preserves 409 conflicts for optimistic UI recovery", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ detail: "HR position conflict" }), { status: 409 },
  )));

  await expect(
    createHrApi("csrf").mergeDraft(DRAFT_ID, POSITION_ID, 1, REQUEST_ID),
  ).rejects.toMatchObject({ status: 409 });
});
