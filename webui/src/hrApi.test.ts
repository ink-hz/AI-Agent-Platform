/** @vitest-environment jsdom */

import { afterEach, expect, it, vi } from "vitest";

import { createHrApi } from "./hrApi";


const POSITION_ID = "11111111-1111-4111-8111-111111111111";
const DRAFT_ID = "22222222-2222-4222-8222-222222222222";
const DRAFT_VERSION_ID = "66666666-6666-4666-8666-666666666666";
const CONVERSATION_ID = "33333333-3333-4333-8333-333333333333";
const ATTACHMENT_ID = "44444444-4444-4444-8444-444444444444";
const REQUEST_ID = "55555555-5555-4555-8555-555555555555";
const CONTEXT_VERSION_ID = "77777777-7777-4777-8777-777777777777";
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
const positionPackage = {
  draft_id: DRAFT_ID, draft_version_id: DRAFT_VERSION_ID,
  conversation_id: CONVERSATION_ID, version_number: 3,
  title: "高级结构工程师",
  modules: {
    mission: { text: "负责高可靠挤出系统交付。" },
    jd: { text: "负责喷嘴与挤出系统结构设计。" },
    jr: { text: "具备精密机械量产经验。" },
  },
  row_version: 2, created_at: NOW, updated_at: NOW,
};
const confirmedPositionPackage = {
  position_id: POSITION_ID, context_version_id: CONTEXT_VERSION_ID,
  conversation_id: CONVERSATION_ID,
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


it("parses a strict conversation position package and forwards AbortSignal", async () => {
  const signal = new AbortController().signal;
  const fetchMock = vi.fn().mockResolvedValue(new Response(
    JSON.stringify(positionPackage), { status: 200 },
  ));
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    createHrApi("csrf").positionPackage(CONVERSATION_ID, signal),
  ).resolves.toEqual({
    draftId: DRAFT_ID, draftVersionId: DRAFT_VERSION_ID,
    conversationId: CONVERSATION_ID, versionNumber: 3,
    title: "高级结构工程师",
    modules: {
      mission: { text: "负责高可靠挤出系统交付。" },
      jd: { text: "负责喷嘴与挤出系统结构设计。" },
      jr: { text: "具备精密机械量产经验。" },
    },
    rowVersion: 2, createdAt: NOW, updatedAt: NOW,
  });
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/hr/conversations/${CONVERSATION_ID}/position-package`,
    expect.objectContaining({ credentials: "same-origin", signal }),
  );
});


it.each([
  ["malformed UUID", { ...positionPackage, draft_version_id: "not-a-uuid" }],
  ["wrong scalar type", { ...positionPackage, version_number: "3" }],
  ["missing module", {
    ...positionPackage,
    modules: { mission: positionPackage.modules.mission, jd: positionPackage.modules.jd },
  }],
  ["unexpected module field", {
    ...positionPackage,
    modules: { ...positionPackage.modules, jd: { text: "JD", html: "<b>secret</b>" } },
  }],
  ["unexpected response field", { ...positionPackage, hidden_envelope: "secret" }],
])("rejects position packages with %s", async (_label, payload) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify(payload), { status: 200 },
  )));

  await expect(
    createHrApi("csrf").positionPackage(CONVERSATION_ID),
  ).rejects.toThrow("HR position package response invalid");
});


it("confirms a selected package version with strict identifiers and CSRF", async () => {
  const signal = new AbortController().signal;
  const fetchMock = vi.fn().mockResolvedValue(new Response(
    JSON.stringify(confirmedPositionPackage), { status: 200 },
  ));
  vi.stubGlobal("fetch", fetchMock);

  await expect(createHrApi("csrf-current").confirmPositionPackage(
    DRAFT_ID, DRAFT_VERSION_ID, 2, REQUEST_ID, signal,
  )).resolves.toEqual({
    positionId: POSITION_ID,
    contextVersionId: CONTEXT_VERSION_ID,
    conversationId: CONVERSATION_ID,
  });
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/hr/position-drafts/${DRAFT_ID}/versions/${DRAFT_VERSION_ID}/confirm`,
    expect.objectContaining({
      credentials: "same-origin",
      method: "POST",
      signal,
      body: JSON.stringify({ expected_row_version: 2 }),
    }),
  );
  const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
  expect(headers.get("X-CSRF-Token")).toBe("csrf-current");
  expect(headers.get("Idempotency-Key")).toBe(REQUEST_ID);
});


it("rejects invalid package request identifiers and row versions before fetch", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const api = createHrApi("csrf");

  await expect(api.positionPackage("not-a-uuid")).rejects.toThrow(
    "HR position package identifier invalid",
  );
  await expect(api.confirmPositionPackage(
    DRAFT_ID, "not-a-uuid", 2, REQUEST_ID,
  )).rejects.toThrow("HR position package identifier invalid");
  await expect(api.confirmPositionPackage(
    DRAFT_ID, DRAFT_VERSION_ID, 0, REQUEST_ID,
  )).rejects.toThrow("HR position package row version invalid");
  expect(fetchMock).not.toHaveBeenCalled();
});


it.each([
  ["missing field", { position_id: POSITION_ID, conversation_id: CONVERSATION_ID }],
  ["invalid context UUID", { ...confirmedPositionPackage, context_version_id: "invalid" }],
  ["unexpected locator", { ...confirmedPositionPackage, artifact_locator: "s3://secret" }],
])("rejects confirmed position packages with %s", async (_label, payload) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify(payload), { status: 200 },
  )));

  await expect(createHrApi("csrf").confirmPositionPackage(
    DRAFT_ID, DRAFT_VERSION_ID, 2, REQUEST_ID,
  )).rejects.toThrow("HR confirmed position package response invalid");
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
    const body = path.includes("/versions/")
      ? confirmedPositionPackage
      : path.includes("/conversations/")
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
  await api.confirmPositionPackage(DRAFT_ID, DRAFT_VERSION_ID, 2, REQUEST_ID);
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
