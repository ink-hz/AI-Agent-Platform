import { describe, expect, it, vi } from "vitest";

import { HrR12ApiError, createHrR12Api } from "./hrR12Api";


const POSITION_ID = "00000000-0000-4000-8000-000000000001";
const REQUEST_ID = "00000000-0000-4000-8000-000000000002";
const ATTACHMENT_ID = "00000000-0000-4000-8000-000000000003";


describe("R1.2 HR API", () => {
  it("uses caller request ids and preserves abort signals for mutations", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ materials: [], artifacts: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ content_path: "/opaque", expires_at: "2026-09-04T00:05:00Z" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await createHrR12Api("csrf").resources(POSITION_ID, controller.signal);
    await createHrR12Api("csrf").downloadResource(POSITION_ID, ATTACHMENT_ID, REQUEST_ID);

    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
    const init = fetchMock.mock.calls[1]?.[1];
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(REQUEST_ID);
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf");
  });

  it("keeps actionable HTTP statuses for UI recovery", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "baseline changed" }), { status: 409 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).rejects.toBeInstanceOf(HrR12ApiError);
    await expect(createHrR12Api("csrf").resources(POSITION_ID)).rejects.toMatchObject({ status: 409 });
  });

  it("normalizes exact material metadata without accepting a storage locator", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ materials: [{
      attachment_id: ATTACHMENT_ID, filename: "岗位说明.pdf", media_type: "application/pdf", state: "ready",
      size_bytes: 2, created_at: "2026-09-04T00:00:00Z", source_conversation_id: null,
      source_turn_id: null, preview_available: true, download_available: true,
    }], artifacts: [] }), { status: 200 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).resolves.toMatchObject({
      materials: [{ attachmentId: ATTACHMENT_ID, filename: "岗位说明.pdf" }],
    });
  });
});
