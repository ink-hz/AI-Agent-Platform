/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { faeWorkbenchApi } from "./faeWorkbenchApi";


const overview = {
  period_start: "2026-08-24T00:00:00+08:00",
  period_end: "2026-08-31T00:00:00+08:00",
  timezone: "Asia/Shanghai",
  freshness: { status: "fresh", data_as_of: "2026-08-31T08:00:00+08:00" },
  summary: {
    state: { status: "available", as_of: "2026-08-31T08:00:00+08:00", error_code: null },
    data: {
      session_count: 12, active_subject_count: 8, negative_feedback_events: 2, negative_turn_count: 2,
      abnormal_session_count: 1, open_issue_count: null, p50_duration_ms: 110, p95_duration_ms: 300,
    },
  },
  attention: {
    state: { status: "available", as_of: "2026-08-31T08:00:00+08:00", error_code: null },
    items: [{ session_key: "fae:session-1", title: null, last_active_at: "2026-08-30T12:00:00+08:00", reason: "fallback" }],
  },
  trends: {
    state: { status: "available", as_of: "2026-08-31T08:00:00+08:00", error_code: null },
    points: [{ day: "2026-08-30", sessions: 2, negative_turns: 1 }],
  },
  issues: {
    state: { status: "unavailable", as_of: null, error_code: "issues_unavailable" }, statuses: {},
  },
  reports: {
    state: { status: "unavailable", as_of: null, error_code: "reports_not_integrated" },
  },
};


afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });


describe("FAE workbench API", () => {
  it("preserves nullable metrics and unavailable sections from a valid overview", async () => {
    const value = {
      ...overview,
      summary: {
        ...overview.summary,
        data: { ...overview.summary.data, p50_duration_ms: null, p95_duration_ms: null },
      },
      attention: {
        state: { status: "unavailable", as_of: null, error_code: "operational_summary_unavailable" }, items: [],
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));

    await expect(faeWorkbenchApi.overview()).resolves.toMatchObject({
      summary: { data: { open_issue_count: null, p50_duration_ms: null, p95_duration_ms: null } },
      attention: { state: { status: "unavailable", as_of: null, error_code: "operational_summary_unavailable" }, items: [] },
      issues: { state: { status: "unavailable", as_of: null, error_code: "issues_unavailable" } },
      reports: { state: { status: "unavailable", as_of: null, error_code: "reports_not_integrated" } },
    });
  });

  it("sends only server-accepted session filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [], total: 0, limit: 20, offset: 0,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await faeWorkbenchApi.listSessions({
      q: "fallback", channel: "dingtalk", sentiment: "negative", review_status: "pending",
      outcome: "failed", date_from: "2026-08-24T00:00:00+08:00", date_to: "2026-08-31T00:00:00+08:00",
      limit: 20, offset: 0, agent_id: "other-agent", source_kind: "admin",
    } as never);

    const requestPath = String(fetchMock.mock.calls[0][0]);
    expect(requestPath).toContain("/api/admin/fae/sessions?");
    expect(requestPath).toContain("q=fallback");
    expect(requestPath).toContain("channel=dingtalk");
    expect(requestPath).not.toContain("agent_id");
    expect(requestPath).not.toContain("source_kind");
    expect(requestPath).not.toContain("environment");
  });

  it("rejects overview data missing an explicit section state", async () => {
    const { state: _state, ...summaryWithoutState } = overview.summary;
    void _state;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...overview, summary: summaryWithoutState,
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(faeWorkbenchApi.overview()).rejects.toThrow("FAE workbench response contract invalid");
  });

  it.each([
    ["unknown", "2026-08-31T08:00:00+08:00"],
    ["fresh", "2026-08-31T08:00:00"],
    ["fresh", "2026-02-30T08:00:00+08:00"],
  ])("rejects invalid freshness and non-timezone timestamps", async (status, dataAsOf) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...overview, freshness: { status, data_as_of: dataAsOf },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(faeWorkbenchApi.overview()).rejects.toThrow("FAE workbench response contract invalid");
  });

  it("implements the complete scoped Review API and never sends browser agent scope", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({}), {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    vi.stubGlobal("fetch", fetchMock);
    const actor = "corp:00000000-0000-0000-0000-000000000099";
    const payload = { agent_id: "browser-controlled", reason: "test" };

    await faeWorkbenchApi.review.overview();
    await faeWorkbenchApi.review.inbox();
    await faeWorkbenchApi.review.issues();
    await faeWorkbenchApi.review.turnSummaries(["fae:turn-1"]);
    await faeWorkbenchApi.review.issue("00000000-0000-0000-0000-000000000001");
    await faeWorkbenchApi.review.create(payload, actor);
    await faeWorkbenchApi.review.link("00000000-0000-0000-0000-000000000001", payload, actor);
    await faeWorkbenchApi.review.update("00000000-0000-0000-0000-000000000001", payload, actor);
    await faeWorkbenchApi.review.move("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", payload, actor);
    await faeWorkbenchApi.review.fixReady("00000000-0000-0000-0000-000000000001", payload, actor);
    await faeWorkbenchApi.review.merge("00000000-0000-0000-0000-000000000001", payload, actor);
    await faeWorkbenchApi.review.addEvidence("00000000-0000-0000-0000-000000000001", payload, actor);
    await faeWorkbenchApi.review.verifyEvidence("00000000-0000-0000-0000-000000000003", actor);
    await faeWorkbenchApi.review.replay("00000000-0000-0000-0000-000000000001", payload, actor);
    await faeWorkbenchApi.review.semanticReview("00000000-0000-0000-0000-000000000004", payload, actor);
    await faeWorkbenchApi.review.disposition("00000000-0000-0000-0000-000000000001", payload, actor);

    const paths = fetchMock.mock.calls.map(([path]) => String(path));
    expect(paths).toEqual([
      "/api/admin/fae/issue-overview",
      "/api/admin/fae/issue-inbox?limit=200",
      "/api/admin/fae/issues?limit=200",
      "/api/admin/fae/turn-summaries?turn_key=fae%3Aturn-1",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001",
      "/api/admin/fae/issues",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001/links",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001/links/00000000-0000-0000-0000-000000000002/move",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001/fix-ready",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001/merge",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001/evidence",
      "/api/admin/fae/evidence/00000000-0000-0000-0000-000000000003/verify",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001/replays",
      "/api/admin/fae/replays/00000000-0000-0000-0000-000000000004/semantic-review",
      "/api/admin/fae/issues/00000000-0000-0000-0000-000000000001/disposition",
    ]);
    const createBody = JSON.parse(String(fetchMock.mock.calls[5][1]?.body));
    const linkBody = JSON.parse(String(fetchMock.mock.calls[6][1]?.body));
    expect(createBody).toEqual({ reason: "test" });
    expect(linkBody).toEqual({ reason: "test" });
    expect(fetchMock.mock.calls.slice(5).every(([, init]) => new Headers(init?.headers).get("X-Review-Actor") === actor)).toBe(true);
  });
});
