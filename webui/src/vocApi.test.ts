/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { createVocApi, VocApiError } from "./vocApi";

const draft = {
  draft_id: "11111111-1111-4111-8111-111111111111",
  state: "collecting",
  version: 1,
  source_text: "客户说设备发热",
  content: {
    customer: null,
    feedback: "设备发热",
    product_or_scenario: null,
    impact: null,
    evidence_basis: "employee_relay",
    gaps: ["客户名称未知"],
  },
  submitted_voc_no: null,
  created_at: "2026-08-26T10:00:00Z",
  updated_at: "2026-08-26T10:00:00Z",
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("VOC workspace API", () => {
  it("uses same-origin credentials and CSRF only for mutations", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ...draft, assistant_message: "已整理" }))
      .mockResolvedValueOnce(json({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createVocApi();

    await api.createDraft("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "客户说设备发热", "csrf");
    await api.listVocs();

    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/v1/extensions/voc/drafts",
      expect.objectContaining({
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      }),
    ]);
    expect(fetchMock.mock.calls[1]).toEqual([
      "/api/v1/extensions/voc/vocs?limit=20",
      { credentials: "include" },
    ]);
  });

  it("rejects unknown response fields and exposes stable status only", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ ...draft, extra: "poison" })));
    await expect(createVocApi().activeDraft()).rejects.toThrow("VOC draft response invalid");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ detail: "stale_draft_version" }, 409)));
    const failure = await createVocApi().activeDraft().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(VocApiError);
    expect(failure).toMatchObject({ status: 409, code: "stale_draft_version" });
  });
});
