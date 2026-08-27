/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { createVocAdminApi, VocAdminApiError } from "./vocAdminApi";

const USER_ID = "11111111-1111-4111-8111-111111111111";
const summary = {
  voc_no: "VOC-20260826-001",
  submitter_internal_user_id: USER_ID,
  submitter_name: "苍渊",
  source: "platform",
  latest_content: "设备连续运行三小时后明显发热",
  revision: 2,
  analysis_status: "claimed",
  created_at: "2026-08-26T09:30:00Z",
  updated_at: "2026-08-26T09:35:00Z",
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("VOC management API", () => {
  it("serializes only selected filters and forwards cancellation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ items: [summary], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await createVocAdminApi().list({
      query: "发热",
      submitterInternalUserId: USER_ID,
      legacySubmitterName: null,
      createdFrom: "2026-08-01T00:00:00.000Z",
      createdTo: "2026-09-01T00:00:00.000Z",
      cursor: null,
      limit: 50,
    }, controller.signal);

    const [url, init] = fetchMock.mock.calls[0];
    const parsed = new URL(url, "https://agent.example.test");
    expect(parsed.pathname).toBe("/api/v1/extensions/voc/admin/vocs");
    expect(Object.fromEntries(parsed.searchParams)).toEqual({
      query: "发热",
      submitter_internal_user_id: USER_ID,
      created_from: "2026-08-01T00:00:00.000Z",
      created_to: "2026-09-01T00:00:00.000Z",
      limit: "50",
    });
    expect(init).toEqual({ credentials: "include", signal: controller.signal });
  });

  it("strictly parses summaries, details, entries, and submitters", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ ...summary, entries: [{
        revision: 1,
        entry_type: "original",
        content: "设备发热",
        created_at: "2026-08-26T09:30:00Z",
      }] }))
      .mockResolvedValueOnce(json({ items: [{ internal_user_id: USER_ID, display_name: "苍渊" }] }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const api = createVocAdminApi();

    const detail = await api.detail("VOC-20260826-001/unsafe", controller.signal);
    const submitters = await api.submitters(controller.signal);

    expect(detail.entries[0].entry_type).toBe("original");
    expect(submitters).toEqual([{ internal_user_id: USER_ID, display_name: "苍渊" }]);
    expect(fetchMock.mock.calls[0][0]).toContain("VOC-20260826-001%2Funsafe");
  });

  it("accepts attachment-only history with empty text content", async () => {
    const attachmentOnly = { ...summary, latest_content: "" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ items: [attachmentOnly], next_cursor: null }))
      .mockResolvedValueOnce(json({
        ...attachmentOnly,
        entries: [{
          revision: 1,
          entry_type: "original",
          content: "",
          created_at: "2026-08-26T09:30:00Z",
        }],
      }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createVocAdminApi();
    const signal = new AbortController().signal;

    const page = await api.list({}, signal);
    const detail = await api.detail(summary.voc_no, signal);

    expect(page.items[0].latest_content).toBe("");
    expect(detail.entries[0].content).toBe("");
  });

  it.each([
    { ...summary, unexpected: true },
    { ...summary, revision: 0 },
    { ...summary, analysis_status: "unknown" },
    { ...summary, source: "email" },
    { ...summary, created_at: "yesterday" },
    { ...summary, submitter_internal_user_id: "not-a-uuid" },
  ])("rejects an invalid summary contract", async (invalid) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ items: [invalid], next_cursor: null })));
    await expect(createVocAdminApi().list({}, new AbortController().signal))
      .rejects.toThrow("VOC management list response invalid");
  });

  it("rejects detail unknown fields and exposes only stable API failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ ...summary, entries: [], poison: true })));
    await expect(createVocAdminApi().detail(summary.voc_no, new AbortController().signal))
      .rejects.toThrow("VOC management detail response invalid");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ detail: "forbidden", private: "secret" }, 403)));
    const failure = await createVocAdminApi().submitters(new AbortController().signal)
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(VocAdminApiError);
    expect(failure).toMatchObject({ status: 403, code: "forbidden" });
  });
});
