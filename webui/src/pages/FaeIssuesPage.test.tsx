/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { useRoute } from "../router";
import type { FeedbackIssueDetail, ReviewOverview, SessionDetail, TurnDetail } from "../types";
import { FaeIssuesPage } from "./FaeIssuesPage";


const ISSUE_ID = "00000000-0000-0000-0000-000000000001";
const owner: Account = {
  internal_user_id: "00000000-0000-0000-0000-000000000099",
  display_name: "平台负责人",
  role: "platform_owner",
  departments: ["AI"],
  gender: null,
  observation_agent_ids: [],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf",
};
const ordinaryTurn: TurnDetail = {
  turn_key: "fae:turn-1", session_key: "fae:session-1", agent_id: "ai-fae-agent",
  source_kind: "fae", turn_index: 1, question: "普通回答", answer: "没有负反馈也需要治理",
  created_at: "2026-08-31T00:00:00Z", question_at: null, answer_at: null,
  question_time_status: "unavailable", answer_time_status: "unavailable", trace_key: null,
  outcome: "resolved", fallback_used: false, duration_ms: 20, sources: [], evidence: [],
  evidence_availability: "available", feedback: [], reviews: [], improvements: [],
  input_attachments: [], output_attachments: [], details: {}, sender_name: null,
  sender_department: null, sender_identity_status: "unavailable",
};
const session: SessionDetail = {
  session_key: "fae:session-1", agent_id: "ai-fae-agent", source_kind: "fae", channel: "DingTalk",
  title: "FAE Session", created_at: "2026-08-31T00:00:00Z", last_active_at: "2026-08-31T00:01:00Z",
  turn_count: 1, feedback_count: 0, review_count: 0, latest_outcome: "resolved",
  source_synced_at: "2026-08-31T00:02:00Z", freshness: "fresh", participant_count: 1,
  primary_sender_name: null, primary_sender_department: null, sender_identity_status: "unavailable",
  turns: [ordinaryTurn],
};
const overview = (writeAvailable: boolean): ReviewOverview => ({
  feedback_rows: 0, negative_rows: 0, negative_turns: 0, positive_rows: 0, issue_total: 0,
  statuses: {}, dispositions: {}, write_available: writeAvailable,
});
const detail: FeedbackIssueDetail = {
  issue: {
    id: ISSUE_ID, agent_id: "ai-fae-agent", origin_turn_key: ordinaryTurn.turn_key,
    title: "普通回答治理事项", priority: "P2", failure_layer: "synthesis", secondary_layers: [],
    root_cause: "回答缺少约束", impact_scope: "FAE", owner: null, disposition: "actionable", row_version: 1,
  },
  progress: { issue_id: ISSUE_ID, status: "fixing", missing_gates: ["fix_ready"], replay_passed_turns: 0, replay_required_turns: 1, reopened: false },
  links: [{
    id: "00000000-0000-0000-0000-000000000002", active: true, link_role: "primary",
    agent_id: "ai-fae-agent", source_turn_key: ordinaryTurn.turn_key, source_feedback_keys: [],
    source_question: ordinaryTurn.question, source_answer: ordinaryTurn.answer,
  }],
  evidence: [], replays: [], events: [],
};

function RouteHarness({ account = owner }: { account?: Account }) {
  const route = useRoute();
  return route.name === "admin-fae-issue"
    ? <FaeIssuesPage account={account} issueId={route.issueId} />
    : <FaeIssuesPage account={account} />;
}

function response(body: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 404, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}

function errorResponse(status: number, detail = "unavailable"): Response {
  return { ok: false, status, json: vi.fn().mockResolvedValue({ detail }) } as unknown as Response;
}

describe("FaeIssuesPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("creates governance from the exact deep-linked real Turn and opens its stable Issue URL", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1");
    const writes: { path: string; body: Record<string, unknown>; headers: Headers }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/admin/fae/sessions/fae%3Asession-1") return response(session);
      if (path === "/api/admin/fae/issue-overview") return response(overview(true));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return response([]);
      if (path.startsWith("/api/admin/fae/issues?")) return response([]);
      if (init?.method === "POST") {
        writes.push({ path, body: JSON.parse(String(init.body)), headers: new Headers(init.headers) });
        return response(detail);
      }
      if (path === `/api/admin/fae/issues/${ISSUE_ID}`) return response(detail);
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(container.textContent).toContain("普通回答");
    expect(container.textContent).toContain("创建事项并纳管");
    expect(container.querySelector('[aria-label="Agent"]')).toBeNull();
    expect(container.textContent).not.toContain("复审身份");
    const seededInbox = container.querySelector<HTMLButtonElement>(".review-inbox button")!;
    await act(async () => seededInbox.click());
    const seedQuery = new URLSearchParams(window.location.search);
    expect(seedQuery.get("session_key")).toBe(session.session_key);
    expect(seedQuery.get("turn_key")).toBe(ordinaryTurn.turn_key);
    expect(container.textContent).toContain("创建事项并纳管");
    const create = [...container.querySelectorAll("button")].find((button) => button.textContent === "创建事项并纳管");
    await act(async () => create?.click());

    const linkRequest = writes.find((item) => item.path.endsWith("/links"));
    expect(linkRequest?.body).toMatchObject({
      source_turn_key: "fae:turn-1",
      source_feedback_keys: [],
    });
    expect(writes[0].body).not.toHaveProperty("agent_id");
    expect(linkRequest?.body).not.toHaveProperty("agent_id");
    expect(writes.every((item) => item.headers.get("X-CSRF-Token") === owner.csrf_token)).toBe(true);
    expect(window.location.pathname).toBe(`/admin/fae/issues/${ISSUE_ID}`);
  });

  it("binds writes to a rotated account CSRF token", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1");
    const writeHeaders: Headers[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/admin/fae/sessions/fae%3Asession-1") return response(session);
      if (path === "/api/admin/fae/issue-overview") return response(overview(true));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return response([]);
      if (path.startsWith("/api/admin/fae/issues?")) return response([]);
      if (init?.method === "POST") {
        writeHeaders.push(new Headers(init.headers));
        return response(detail);
      }
      if (path === `/api/admin/fae/issues/${ISSUE_ID}`) return response(detail);
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));
    await act(async () => root.render(<FaeIssuesPage account={{ ...owner, csrf_token: "csrf-rotated" }} />));
    const create = [...container.querySelectorAll("button")].find((button) => button.textContent === "创建事项并纳管");
    await act(async () => create?.click());

    expect(writeHeaders).toHaveLength(2);
    expect(writeHeaders.every((headers) => headers.get("X-CSRF-Token") === "csrf-rotated")).toBe(true);
  });

  it("keeps FAE inbox URL state fixed-scope without an agent query", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(true)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([{
        agent_id: "ai-fae-agent", turn_key: ordinaryTurn.turn_key, question: ordinaryTurn.question,
        answer: ordinaryTurn.answer, feedback_keys: [], first_feedback_at: ordinaryTurn.created_at,
      }]));
      if (path.startsWith("/api/admin/fae/issues?")) return Promise.resolve(response([]));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));
    const inboxRow = container.querySelector<HTMLButtonElement>(".review-inbox button")!;
    await act(async () => inboxRow.click());

    const query = new URLSearchParams(window.location.search);
    expect(query.get("turn_key")).toBe(ordinaryTurn.turn_key);
    expect(query.has("agent_id")).toBe(false);
  });

  it("pushes stable FAE issue routes and restores list/detail through browser history", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(true)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) return Promise.resolve(response([{ ...detail.issue, progress: detail.progress }]));
      if (path === `/api/admin/fae/issues/${ISSUE_ID}`) return Promise.resolve(response(detail));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<RouteHarness />));
    const issueRow = container.querySelector<HTMLButtonElement>(".review-issue-list button")!;
    await act(async () => issueRow.click());
    expect(window.location.pathname).toBe(`/admin/fae/issues/${ISSUE_ID}`);
    expect(container.textContent).toContain("回答缺少约束");

    await act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(window.location.pathname).toBe("/admin/fae/issues");
    expect(container.textContent).not.toContain("回答缺少约束");

    await act(async () => {
      window.history.forward();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(window.location.pathname).toBe(`/admin/fae/issues/${ISSUE_ID}`);
    expect(container.textContent).toContain("回答缺少约束");
  });

  it("preserves the preview prefix when opening a stable FAE issue", async () => {
    const prefix = "/_preview/dingtalk-r1";
    window.history.replaceState({}, "", `${prefix}/admin/fae/issues`);
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === `${prefix}/api/admin/fae/issue-overview`) return Promise.resolve(response(overview(true)));
      if (path.startsWith(`${prefix}/api/admin/fae/issue-inbox`)) return Promise.resolve(response([]));
      if (path.startsWith(`${prefix}/api/admin/fae/issues?`)) return Promise.resolve(response([{ ...detail.issue, progress: detail.progress }]));
      if (path === `${prefix}/api/admin/fae/issues/${ISSUE_ID}`) return Promise.resolve(response(detail));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<RouteHarness />));
    await act(async () => container.querySelector<HTMLButtonElement>(".review-issue-list button")!.click());

    expect(window.location.pathname).toBe(`${prefix}/admin/fae/issues/${ISSUE_ID}`);
    expect(container.textContent).toContain("回答缺少约束");
  });

  it("rejects a deep link whose Turn is absent from the scoped Session", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Amissing");
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/admin/fae/sessions/fae%3Asession-1") return Promise.resolve(response(session));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(container.textContent).toContain("找不到原始回答");
    expect(container.textContent).not.toContain("创建事项并纳管");
  });

  it.each([
    [401, "无权读取原始回答"],
    [403, "无权读取原始回答"],
    [503, "原始回答暂不可用"],
  ])("distinguishes Session operational failure %s from a missing Turn", async (status, expected) => {
    window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(errorResponse(status)));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(container.textContent).toContain(expected);
    expect(container.textContent).not.toContain("找不到原始回答");
    expect(container.textContent).not.toContain("创建事项并纳管");
  });

  it("treats a malformed Session response as operationally unavailable", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ turns: "invalid" })));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(container.textContent).toContain("原始回答暂不可用");
    expect(container.textContent).not.toContain("找不到原始回答");
  });

  it("does not retain a loaded Turn when the FAE deep-link URL changes", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1");
    const pendingSession = new Promise<Response>(() => undefined);
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/admin/fae/sessions/fae%3Asession-1") return Promise.resolve(response(session));
      if (path === "/api/admin/fae/sessions/fae%3Asession-pending") return pendingSession;
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(true)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) return Promise.resolve(response([]));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));
    expect(container.textContent).toContain("普通回答");

    window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-pending&turn_key=fae%3Aturn-pending");
    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(container.textContent).toContain("正在加载原始回答");
    expect(container.textContent).not.toContain("普通回答");
  });

  it("renders projected Issue state without any mutation controls in a cloud replica", async () => {
    window.history.replaceState({}, "", `/admin/fae/issues/${ISSUE_ID}`);
    const projectedOverview = {
      feedback_rows: null, negative_rows: null, negative_turns: null, positive_rows: null,
      feedback_totals_status: "unavailable", issue_total: 1,
      statuses: { actionable: 1 }, dispositions: { actionable: 1 }, write_available: false,
    };
    const projectedIssue = {
      id: ISSUE_ID, agent_id: "ai-fae-agent", title: "脱敏治理事项", priority: "P2",
      failure_layer: "synthesis", owner: null, disposition: "actionable",
      updated_at: "2026-08-31T00:00:00Z", linked_turn_count: 2, replica_read_only: true,
      progress: { status: "actionable", missing_gates: [] },
    };
    const projectedDetail = {
      issue: projectedIssue, links: [], evidence: [], replays: [], events: [],
      progress: projectedIssue.progress, replica_read_only: true,
    };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (init?.method && init.method !== "GET") throw new Error(`Unexpected mutation: ${path}`);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(projectedOverview));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) return Promise.resolve(response([projectedIssue]));
      if (path === `/api/admin/fae/issues/${ISSUE_ID}`) return Promise.resolve(response(projectedDetail));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(container.textContent).toContain("脱敏治理事项");
    expect(container.textContent).toContain("当前为只读副本");
    expect(container.textContent).toContain("可处理事项");
    expect(container.textContent).toContain("生命周期状态暂不可用");
    expect(container.textContent).toContain("闭环门：暂不可用");
    expect(container.textContent).not.toContain("undefined");
    expect(container.textContent).not.toContain("0/0");
    expect(container.textContent).not.toContain("所有硬门均已满足");
    const buttonLabels = [...container.querySelectorAll("button")].map((button) => button.textContent);
    ["创建事项并纳管", "保存归因", "关联到已有事项", "添加证据", "复跑 fae:turn-1", "无需处理"]
      .forEach((label) => expect(buttonLabels).not.toContain(label));
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("hides mutations and shows paused governance when directory data is hard stale", async () => {
    window.history.replaceState({}, "", `/admin/fae/issues/${ISSUE_ID}`);
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (init?.method && init.method !== "GET") throw new Error(`Unexpected mutation: ${path}`);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(true)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) return Promise.resolve(response([{ ...detail.issue, progress: detail.progress }]));
      if (path === `/api/admin/fae/issues/${ISSUE_ID}`) return Promise.resolve(response(detail));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<FaeIssuesPage account={{ ...owner, hard_stale_read_only: true }} />));

    expect(container.textContent).toContain("治理变更已暂停");
    expect(container.textContent).not.toContain("当前为只读副本");
    const buttons = [...container.querySelectorAll("button")].map((button) => button.textContent);
    expect(buttons).not.toContain("保存归因");
    expect(buttons).not.toContain("添加证据");
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
});
