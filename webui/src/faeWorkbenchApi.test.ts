/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { faeWorkbenchApi } from "./faeWorkbenchApi";
import type { ReviewApi } from "./components/review/ReviewWorkspace";


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
    report_id: null, title: null, data_cutoff_at: null, currentness: null,
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
      reports: { state: { status: "unavailable", as_of: null, error_code: "reports_not_integrated" }, report_id: null, title: null, data_cutoff_at: null, currentness: null },
    });
  });

  it("sends only server-accepted session filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [], total: 0, limit: 20, offset: 0,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await faeWorkbenchApi.listSessions({
      q: "fallback", channel: "dingtalk", sentiment: "negative", review_status: "pending",
      outcome: "failed", date_from: "2026-08-24T00:00:00+08:00", date_to: "2026-08-30T23:59:59+08:00",
      date_before: "2026-08-31T00:00:00+08:00",
      limit: 20, offset: 0, agent_id: "other-agent", source_kind: "admin",
    } as never);

    const requestPath = String(fetchMock.mock.calls[0][0]);
    expect(requestPath).toContain("/api/fae/sessions?");
    expect(requestPath).toContain("q=fallback");
    expect(requestPath).toContain("channel=dingtalk");
    expect(requestPath).toContain("date_to=2026-08-30T23%3A59%3A59%2B08%3A00");
    expect(requestPath).toContain("date_before=2026-08-31T00%3A00%3A00%2B08%3A00");
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
    const projectedIssue = {
      id: "00000000-0000-0000-0000-000000000001", agent_id: "ai-fae-agent", title: "脱敏事项",
      priority: "P2", failure_layer: null, owner: null, disposition: "actionable",
      updated_at: "2026-08-31T00:00:00Z", linked_turn_count: 1, replica_read_only: true,
      progress: { status: "actionable", missing_gates: [] },
    };
    const projectedInbox = {
      agent_id: "ai-fae-agent", turn_key: "fae:turn-projected", feedback_count: 3,
      feedback_keys: [], first_feedback_at: "2026-08-31T00:00:00Z",
    };
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      let body: unknown = {};
      if (!init?.method && path.endsWith("/issue-overview")) body = {
        feedback_rows: null, negative_rows: null, negative_turns: null, positive_rows: null,
        feedback_totals_status: "unavailable", issue_total: 1,
        statuses: { actionable: 1 }, dispositions: { actionable: 1 }, write_available: false,
      };
      else if (!init?.method && path.includes("issue-inbox")) body = [projectedInbox];
      else if (!init?.method && path.includes("issues?limit")) body = {
        items: [projectedIssue], total: 1, limit: 50, offset: 0, has_more: false,
      };
      else if (!init?.method && path.includes("turn-summaries")) body = [];
      else if (!init?.method && path.includes("/issues/")) body = {
        issue: projectedIssue, progress: projectedIssue.progress,
        links: [], evidence: [], replays: [], events: [], replica_read_only: true,
      };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const actor = "corp:00000000-0000-0000-0000-000000000099";
    const payload = {
      agent_id: "browser-controlled",
      source_kind: "browser-controlled",
      reason: "test",
    };
    const reviewFactory = faeWorkbenchApi.review as unknown as (csrfToken: string) => ReviewApi;

    expect(typeof reviewFactory).toBe("function");
    const review = reviewFactory("csrf-current-account");

    const normalizedOverview = await review.overview();
    const normalizedInbox = await review.inbox();
    const normalizedIssues = await review.issues(undefined, { disposition: "actionable" });
    await review.turnSummaries(["fae:turn-1"]);
    const normalizedDetail = await review.issue("00000000-0000-0000-0000-000000000001");
    await review.create(payload, actor);
    await review.link("00000000-0000-0000-0000-000000000001", payload, actor);
    await review.update("00000000-0000-0000-0000-000000000001", payload, actor);
    await review.move("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", payload, actor);
    await review.fixReady("00000000-0000-0000-0000-000000000001", payload, actor);
    await review.merge("00000000-0000-0000-0000-000000000001", payload, actor);
    await review.addEvidence("00000000-0000-0000-0000-000000000001", payload, actor);
    await review.verifyEvidence("00000000-0000-0000-0000-000000000003", actor);
    await review.replay("00000000-0000-0000-0000-000000000001", payload, actor);
    await review.semanticReview("00000000-0000-0000-0000-000000000004", payload, actor);
    await review.disposition("00000000-0000-0000-0000-000000000001", payload, actor);

    expect(normalizedOverview).toMatchObject({
      feedback_rows: null, negative_rows: null, lifecycle_status_available: false,
      statuses: {}, dispositions: { actionable: 1 },
    });
    expect(normalizedInbox[0]).toMatchObject({ feedback_count: 3, question: "", answer: "" });
    expect(Array.isArray(normalizedIssues) ? normalizedIssues[0] : normalizedIssues.items[0]).toMatchObject({
      disposition: "actionable", root_cause: null,
      progress: { status: "unknown", missing_gates: null, replay_passed_turns: null },
    });
    expect(normalizedDetail).toMatchObject({
      issue: { disposition: "actionable", impact_scope: null },
      progress: { status: "unknown", replay_required_turns: null },
    });

    const paths = fetchMock.mock.calls.map(([path]) => String(path));
    expect(paths).toEqual([
      "/api/fae/issue-overview",
      "/api/fae/issue-inbox?limit=20",
      "/api/fae/issues?limit=20&disposition=actionable",
      "/api/fae/turn-summaries?turn_key=fae%3Aturn-1",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001",
      "/api/fae/issues",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001/links",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001/links/00000000-0000-0000-0000-000000000002/move",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001/fix-ready",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001/merge",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001/evidence",
      "/api/fae/evidence/00000000-0000-0000-0000-000000000003/verify",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001/replays",
      "/api/fae/replays/00000000-0000-0000-0000-000000000004/semantic-review",
      "/api/fae/issues/00000000-0000-0000-0000-000000000001/disposition",
    ]);
    const createBody = JSON.parse(String(fetchMock.mock.calls[5][1]?.body));
    const linkBody = JSON.parse(String(fetchMock.mock.calls[6][1]?.body));
    expect(createBody).toEqual({ reason: "test" });
    expect(linkBody).toEqual({ reason: "test" });
    const mutationBodies = fetchMock.mock.calls.slice(5).map(([, init]) => JSON.parse(String(init?.body)));
    expect(mutationBodies.every((body) => !("agent_id" in body) && !("source_kind" in body))).toBe(true);
    expect(fetchMock.mock.calls.slice(5).every(([, init]) => new Headers(init?.headers).get("X-Review-Actor") === actor)).toBe(true);
    expect(fetchMock.mock.calls.slice(0, 5).every(([, init]) => !new Headers(init?.headers).has("X-CSRF-Token"))).toBe(true);
    expect(paths[2]).not.toContain("agent_id");
    expect(fetchMock.mock.calls.slice(5).every(([, init]) => new Headers(init?.headers).get("X-CSRF-Token") === "csrf-current-account")).toBe(true);

    const changedAccountReview = reviewFactory("csrf-changed-account");
    await changedAccountReview.disposition("00000000-0000-0000-0000-000000000001", payload, actor);
    expect(new Headers(fetchMock.mock.calls[fetchMock.mock.calls.length - 1]?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-changed-account");
  });

  it("preserves authoritative schema-v1 lifecycle and quarantine totals", async () => {
    const issue = {
      id: "00000000-0000-0000-0000-000000000010",
      agent_id: "ai-fae-agent",
      title: "待复跑事项",
      priority: "P1",
      failure_layer: "coverage",
      owner: "codex",
      disposition: "actionable",
      detail_schema_version: 1,
      replica_read_only: true,
      progress: {
        status: "awaiting_replay",
        missing_gates: ["semantic_review"],
      },
    };
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      const body = path.endsWith("/issue-overview")
        ? {
            feedback_rows: 126,
            negative_rows: 90,
            issue_total: 87,
            statuses: { pending_triage: 78, awaiting_replay: 1, closed: 6 },
            dispositions: { actionable: 85, duplicate: 2 },
            lifecycle_status_available: true,
            quarantined_issue_count: 7,
            write_available: false,
          }
        : { items: [issue], total: 1, limit: 20, offset: 0, has_more: false };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }));
    const review = faeWorkbenchApi.review("csrf");

    await expect(review.overview()).resolves.toMatchObject({
      lifecycle_status_available: true,
      statuses: { pending_triage: 78, awaiting_replay: 1, closed: 6 },
      quarantined_issue_count: 7,
    });
    await expect(review.issues(undefined, { limit: 20, status: "open" })).resolves.toMatchObject({
      items: [{ progress: { status: "awaiting_replay", missing_gates: ["semantic_review"] } }],
    });
  });
});
