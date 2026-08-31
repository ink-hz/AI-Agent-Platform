/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import App from "../App";
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
  return {
    ok,
    status: ok ? 200 : 404,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
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
    document.querySelectorAll('meta[name="platform-identity-mode"]').forEach((meta) => meta.remove());
    window.history.replaceState({}, "", "/");
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

  it("restores FAE Issue status filters from URL history and keeps filtered rows actionable", async () => {
    const triageId = "00000000-0000-0000-0000-000000000010";
    const triage = {
      ...detail.issue,
      id: triageId,
      title: "待归因事项",
      progress: { ...detail.progress, issue_id: triageId, status: "pending_triage" },
    };
    const fixing = { ...detail.issue, progress: detail.progress };
    window.history.replaceState({}, "", "/admin/fae/issues?status=pending_triage");
    const issueRequests: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(true)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) {
        issueRequests.push(path);
        return Promise.resolve(response(path.includes("status=fixing") ? [fixing] : [triage]));
      }
      if (path === `/api/admin/fae/issues/${triageId}`) return Promise.resolve(response({
        ...detail, issue: { ...detail.issue, id: triageId, title: triage.title }, progress: triage.progress,
      }));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<RouteHarness />));
    const status = container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')!;
    expect(status.value).toBe("pending_triage");
    expect(container.querySelectorAll(".review-issue-list button")).toHaveLength(1);
    expect(container.textContent).toContain("待归因事项");
    expect(container.textContent).not.toContain("普通回答治理事项");
    expect(issueRequests[issueRequests.length - 1]).toBe("/api/admin/fae/issues?limit=200&status=pending_triage");

    await act(async () => container.querySelector<HTMLButtonElement>(".review-issue-list button")!.click());
    expect(window.location.pathname).toBe(`/admin/fae/issues/${triageId}`);
    expect(window.location.search).toBe("");
    await act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(window.location.pathname).toBe("/admin/fae/issues");
    expect(window.location.search).toBe("?status=pending_triage");
    expect(container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')?.value).toBe("pending_triage");

    const restored = container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(restored, "fixing");
      restored.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(window.location.search).toBe("?status=fixing");
    expect(container.querySelectorAll(".review-issue-list button")).toHaveLength(1);
    expect(container.textContent).toContain("普通回答治理事项");
    expect(container.textContent).not.toContain("待归因事项");
    expect(issueRequests[issueRequests.length - 1]).toBe("/api/admin/fae/issues?limit=200&status=fixing");

    await act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')?.value).toBe("pending_triage");
    await act(async () => {
      window.history.forward();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')?.value).toBe("fixing");
    expect(container.textContent).toContain("普通回答治理事项");
  });

  it("normalizes a cloud-only status bookmark in local mode without sending it to the API", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues?status=actionable");
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      requests.push(path);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(false)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) return Promise.resolve(response([{ ...detail.issue, replica_read_only: true, progress: { status: "actionable", missing_gates: [] } }]));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(requests).toContain("/api/admin/fae/issues?limit=200");
    expect(requests.every((path) => !path.includes("actionable"))).toBe(true);
    expect(requests.every((path) => !path.includes("agent_id"))).toBe(true);
    expect(container.querySelectorAll(".review-issue-list button")).toHaveLength(1);
    expect(container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')?.value).toBe("");
    expect(window.location.search).toBe("");
  });

  it.each([
    "pending_triage", "fixing", "awaiting_merge", "awaiting_deploy", "awaiting_replay",
    "awaiting_review", "closed", "duplicate", "not_actionable", "wont_fix",
  ])("sends local lifecycle filter %s as status and never offers unknown", async (lifecycle) => {
    window.history.replaceState({}, "", `/admin/fae/issues?status=${lifecycle}`);
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      requests.push(path);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(true)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) return Promise.resolve(response([{ ...detail.issue, progress: { ...detail.progress, status: lifecycle } }]));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(requests).toContain(`/api/admin/fae/issues?limit=200&status=${lifecycle}`);
    const options = [...container.querySelectorAll<HTMLOptionElement>('select[aria-label="状态"] option')].map((option) => option.value);
    expect(options).not.toContain("actionable");
    expect(options).not.toContain("unknown");
    expect(container.querySelectorAll(".review-issue-list button")).toHaveLength(1);
  });

  it.each([
    ["actionable", "需处理"],
    ["duplicate", "重复事项"],
    ["not_actionable", "无需处理"],
    ["wont_fix", "暂不修复"],
  ])("sends cloud projected filter %s as disposition and labels returned rows", async (disposition, label) => {
    const identityMeta = document.createElement("meta");
    identityMeta.name = "platform-identity-mode";
    identityMeta.content = "enabled";
    document.head.append(identityMeta);
    window.history.replaceState({}, "", `/admin/fae/issues?disposition=${disposition}`);
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const path = String(input);
      requests.push(path);
      if (path.endsWith("/api/v1/account")) return response(owner);
      if (path.endsWith("/api/deployment")) return response({
        mode: "cloud-replica", read_only: true, auth: "ssh-tunnel", freshness: "current", last_success_at: null,
      });
      if (path.endsWith("/api/admin/fae/issue-overview")) return response({
        feedback_rows: null, negative_rows: null, negative_turns: null, positive_rows: null,
        feedback_totals_status: "unavailable", issue_total: 1,
        statuses: { [disposition]: 1 }, dispositions: { [disposition]: 1 }, write_available: false,
      });
      if (path.includes("/api/admin/fae/issue-inbox")) return response([]);
      if (path.includes("/api/admin/fae/issues?")) return response([{
        ...detail.issue, disposition, replica_read_only: true,
        progress: { status: disposition, missing_gates: [] },
      }]);
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(requests).toContain(`/api/admin/fae/issues?limit=200&disposition=${disposition}`);
    const options = [...container.querySelectorAll<HTMLOptionElement>('select[aria-label="状态"] option')].map((option) => option.value);
    expect(options).toEqual(["", "open", "actionable", "duplicate", "not_actionable", "wont_fix"]);
    expect(options).not.toContain("unknown");
    expect(container.querySelector(".review-issue-list")?.textContent).toContain(label);
    expect(window.location.search).toBe(`?disposition=${disposition}`);
  });

  it("keeps cloud open canonical and normalizes an unknown bookmark without issuing a 422", async () => {
    const identityMeta = document.createElement("meta");
    identityMeta.name = "platform-identity-mode";
    identityMeta.content = "enabled";
    document.head.append(identityMeta);
    window.history.replaceState({}, "", "/admin/fae/issues?status=unknown");
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const path = String(input);
      requests.push(path);
      if (path.endsWith("/api/v1/account")) return response(owner);
      if (path.endsWith("/api/deployment")) return response({
        mode: "cloud-replica", read_only: true, auth: "ssh-tunnel", freshness: "current", last_success_at: null,
      });
      if (path.endsWith("/api/admin/fae/issue-overview")) return response({
        feedback_rows: null, negative_rows: null, negative_turns: null, positive_rows: null,
        feedback_totals_status: "unavailable", issue_total: 0,
        statuses: {}, dispositions: {}, write_available: false,
      });
      if (path.includes("/api/admin/fae/issue-inbox")) return response([]);
      if (path.includes("/api/admin/fae/issues?")) return response([]);
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(`${window.location.pathname}${window.location.search}`).toBe("/admin/fae/issues");
    expect(requests).toContain("/api/admin/fae/issues?limit=200");
    expect(requests.every((path) => !path.includes("status=unknown"))).toBe(true);
    expect(container.textContent).toContain("反馈修复闭环");

    const status = container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(status, "open");
      status.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(requests).toContain("/api/admin/fae/issues?limit=200&status=open");
  });

  it("waits for delayed cloud mode and canonicalizes locally-valid status before the first Issue request", async () => {
    const identityMeta = document.createElement("meta");
    identityMeta.name = "platform-identity-mode";
    identityMeta.content = "enabled";
    document.head.append(identityMeta);
    window.history.replaceState({}, "", "/admin/fae/issues?status=fixing");
    let resolveDeployment!: (value: Response) => void;
    const deployment = new Promise<Response>((resolve) => { resolveDeployment = resolve; });
    const issueRequests: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith("/api/v1/account")) return Promise.resolve(response(owner));
      if (path.endsWith("/api/deployment")) return deployment;
      if (path.endsWith("/api/admin/fae/issue-overview")) return Promise.resolve(response({
        feedback_rows: null, negative_rows: null, negative_turns: null, positive_rows: null,
        feedback_totals_status: "unavailable", issue_total: 0,
        statuses: {}, dispositions: {}, write_available: false,
      }));
      if (path.includes("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.includes("/api/admin/fae/issues?")) {
        issueRequests.push(path);
        return Promise.resolve(response([]));
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(issueRequests).toEqual([]);

    await act(async () => {
      resolveDeployment(response({
        mode: "cloud-replica", read_only: true, auth: "ssh-tunnel", freshness: "current", last_success_at: null,
      }));
      await deployment;
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(issueRequests[0]).toBe("/api/admin/fae/issues?limit=200");
    expect(issueRequests).toHaveLength(1);
    expect(issueRequests.every((path) => !path.includes("fixing"))).toBe(true);
    expect(`${window.location.pathname}${window.location.search}`).toBe("/admin/fae/issues");
  });

  it.each([
    ["cloud disposition", "cloud-replica", "?disposition=actionable", "/api/admin/fae/issues?limit=200&disposition=actionable"],
    ["cloud open", "cloud-replica", "?status=open", "/api/admin/fae/issues?limit=200&status=open"],
    ["local lifecycle", "local", "?status=fixing", "/api/admin/fae/issues?limit=200&status=fixing"],
  ])("issues exactly one initial request for valid %s after mode resolves", async (_label, mode, search, expected) => {
    const identityMeta = document.createElement("meta");
    identityMeta.name = "platform-identity-mode";
    identityMeta.content = "enabled";
    document.head.append(identityMeta);
    window.history.replaceState({}, "", `/admin/fae/issues${search}`);
    let resolveDeployment!: (value: Response) => void;
    const deployment = new Promise<Response>((resolve) => { resolveDeployment = resolve; });
    const issueRequests: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith("/api/v1/account")) return Promise.resolve(response(owner));
      if (path.endsWith("/api/deployment")) return deployment;
      if (path.endsWith("/api/admin/fae/issue-overview")) return Promise.resolve(response(
        mode === "cloud-replica"
          ? { feedback_rows: null, negative_rows: null, negative_turns: null, positive_rows: null, feedback_totals_status: "unavailable", issue_total: 0, statuses: {}, dispositions: {}, write_available: false }
          : overview(true),
      ));
      if (path.includes("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.includes("/api/admin/fae/issues?")) {
        issueRequests.push(path);
        return Promise.resolve(response([]));
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(issueRequests).toEqual([]);

    await act(async () => {
      resolveDeployment(response({
        mode, read_only: mode === "cloud-replica", auth: mode === "cloud-replica" ? "ssh-tunnel" : "dingtalk",
        freshness: "current", last_success_at: null,
      }));
      await deployment;
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(issueRequests).toEqual([expected]);
    expect(window.location.search).toBe(search);
  });

  it("preserves the preview prefix while changing an overview-backed Issue status", async () => {
    const prefix = "/_preview/dingtalk-r1";
    window.history.replaceState({}, "", `${prefix}/admin/fae/issues?status=open`);
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === `${prefix}/api/admin/fae/issue-overview`) return Promise.resolve(response(overview(true)));
      if (path.startsWith(`${prefix}/api/admin/fae/issue-inbox`)) return Promise.resolve(response([]));
      if (path.startsWith(`${prefix}/api/admin/fae/issues?`)) return Promise.resolve(response([{ ...detail.issue, progress: detail.progress }]));
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<RouteHarness />));
    const status = container.querySelector<HTMLSelectElement>('select[aria-label="状态"]')!;
    expect(status.value).toBe("open");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(status, "fixing");
      status.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(`${window.location.pathname}${window.location.search}`).toBe(`${prefix}/admin/fae/issues?status=fixing`);
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

  it("restores server-side Issue filters and page two with accessible pagination", async () => {
    window.history.replaceState({}, "", "/admin/fae/issues?priority=P1&failure_layer=model&owner=corp%3Aone&q=timeout&page=2");
    const issueRequests: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/admin/fae/issue-overview") return Promise.resolve(response(overview(true)));
      if (path.startsWith("/api/admin/fae/issue-inbox")) return Promise.resolve(response([]));
      if (path.startsWith("/api/admin/fae/issues?")) {
        issueRequests.push(path);
        return Promise.resolve(response({
          items: [{ ...detail.issue, priority: "P1", owner: "corp:one", failure_layer: "model", progress: detail.progress }],
          total: 205, limit: 200, offset: 200, has_more: false,
        }));
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    await act(async () => root.render(<FaeIssuesPage account={owner} />));

    expect(issueRequests).toContain(
      "/api/admin/fae/issues?limit=200&offset=200&priority=P1&failure_layer=model&owner=corp%3Aone&q=timeout",
    );
    expect(container.querySelector('nav[aria-label="Issue 分页"]')?.textContent).toContain("第 2 页 · 共 205 项");
    expect(container.querySelector<HTMLInputElement>('input[aria-label="事项搜索"]')?.value).toBe("timeout");
  });
});
