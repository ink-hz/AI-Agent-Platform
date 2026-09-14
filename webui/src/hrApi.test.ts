/** @vitest-environment jsdom */

import { afterEach, expect, it, vi } from "vitest";

import { createHrApi } from "./hrApi";


const POSITION_ID = "11111111-1111-4111-8111-111111111111";
const CONVERSATION_ID = "33333333-3333-4333-8333-333333333333";
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
