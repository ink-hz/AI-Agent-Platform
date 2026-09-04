import { afterEach, describe, expect, it, vi } from "vitest";

import { listAccessEvents, listAccessSubjects } from "./accessHistoryApi";

describe("access history API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses bounded filters and parses a strict response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [{
        access_event_id: "00000000-0000-4000-8000-000000000001",
        display_name: "苍渊", departments: ["总经办"], event_kind: "page_view", login_kind: null,
        workspace_key: "office", page_key: "office.services", module_display_name: "行政服务", page_display_name: "行政服务门户",
        agent_id: null, occurred_at: "2026-09-04T01:02:03Z",
      }], limit: 50, offset: 0, has_more: false,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await listAccessEvents({ display_name: "苍渊", workspace_key: "office", event_kind: "page_view", limit: 50, offset: 0 });
    expect(result.items[0].page_display_name).toBe("行政服务门户");
    expect(String(fetchMock.mock.calls[0][0])).toContain("display_name=%E8%8B%8D%E6%B8%8A");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
  });

  it("parses nickname-indexed subject summaries without internal identity fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [{
        display_name: "苍渊", departments: ["总经办"], event_count: 12,
        latest_occurred_at: "2026-09-04T01:02:03Z", latest_event_kind: "page_view",
        latest_workspace_key: "office", latest_module_display_name: "行政服务",
        latest_page_display_name: "行政服务门户", latest_agent_id: null,
      }], limit: 20, offset: 0, has_more: false,
    }), { status: 200 })));

    const result = await listAccessSubjects({ display_name: "苍" });

    expect(result.items[0].departments).toEqual(["总经办"]);
    expect(result.items[0]).not.toHaveProperty("internal_user_id");
  });

  it("rejects extra fields instead of leaking an expanded backend response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [], limit: 50, offset: 0, has_more: false, session_id: "forbidden",
    }), { status: 200 })));
    await expect(listAccessEvents({})).rejects.toThrow("access history response invalid");
  });

  it("preserves HTTP failure status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 503 })));
    await expect(listAccessEvents({})).rejects.toMatchObject({ status: 503 });
  });
});
