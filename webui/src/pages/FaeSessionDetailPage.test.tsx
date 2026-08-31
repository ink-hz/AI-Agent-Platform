/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionDetail, TurnClosureSummary, TurnDetail } from "../types";
import { FaeSessionDetailPage } from "./FaeSessionDetailPage";


const session: SessionDetail = {
  session_key: "fae:session-1",
  agent_id: "ai-fae-agent",
  source_kind: "fae",
  channel: "DingTalk",
  title: "Gemini 335L troubleshooting",
  created_at: "2026-07-21T08:00:00Z",
  last_active_at: "2026-07-21T09:00:00Z",
  turn_count: 2,
  feedback_count: 1,
  review_count: 1,
  latest_outcome: "resolved",
  source_synced_at: "2026-07-21T09:10:00Z",
  freshness: "fresh",
  participant_count: 1,
  primary_sender_name: null,
  primary_sender_department: null,
  sender_identity_status: "unavailable",
  turns: [],
};

const turnWithoutFeedback: TurnDetail = {
  turn_key: "fae:turn-1", session_key: session.session_key, agent_id: session.agent_id,
  source_kind: "fae", turn_index: 1, question: "问题一", answer: "答案一",
  created_at: "2026-08-03T00:00:00Z", question_at: null, answer_at: null,
  question_time_status: "unavailable", answer_time_status: "unavailable",
  trace_key: null, outcome: "resolved", fallback_used: false, duration_ms: 1,
  sources: [], evidence: [], evidence_availability: "available",
  feedback: [], reviews: [], improvements: [], input_attachments: [], output_attachments: [],
  details: {}, sender_name: null, sender_department: null, sender_identity_status: "unavailable",
};

const negativeTurn: TurnDetail = {
  ...turnWithoutFeedback,
  turn_key: "fae:turn-2",
  turn_index: 2,
  question: "问题二",
  answer: "旧答案",
  feedback: [{
    feedback_key: "fae:feedback-1", sentiment: "negative", raw_rating: "bad",
    reason_code: null, comment: "错误", created_at: "2026-08-03T00:00:00Z", details: {},
  }],
};

function response<T>(body: T): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}


describe("FaeSessionDetailPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    window.history.replaceState({}, "", "/admin/fae/sessions/fae%3Asession-1");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function renderFaeSession(value: SessionDetail, summaries: TurnClosureSummary[] = []) {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === `/api/admin/fae/sessions/${encodeURIComponent(value.session_key)}`) {
        return Promise.resolve(response(value));
      }
      if (path.startsWith("/api/review/turn-summaries?")) return Promise.resolve(response(summaries));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));
    await act(async () => root.render(<FaeSessionDetailPage sessionKey={value.session_key} />));
  }

  it("links every real FAE Turn to Issue governance and presents evidence-backed Session governance", async () => {
    await renderFaeSession({ ...session, turns: [turnWithoutFeedback, negativeTurn] }, [
      {
        turn_key: turnWithoutFeedback.turn_key,
        issue_id: null,
        status: "pending_triage",
        missing_gates: ["issue"],
        latest_valid_replay_id: null,
      },
      {
        turn_key: negativeTurn.turn_key,
        issue_id: "issue-1",
        status: "awaiting_review",
        missing_gates: ["semantic_review"],
        latest_valid_replay_id: "replay-1",
      },
    ]);

    const actions = [...container.querySelectorAll<HTMLAnchorElement>(".review-entry a")];
    expect(actions).toHaveLength(2);
    expect(actions[0].getAttribute("href")).toBe(
      "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1",
    );
    expect(actions[1].textContent).toBe("创建或查看问题");
    const unmanagedEntry = actions[0].closest(".review-entry")!;
    expect(unmanagedEntry.textContent).toContain("尚未纳管");
    expect(unmanagedEntry.textContent).not.toContain("待归因");
    expect(unmanagedEntry.textContent).not.toContain("缺少：纳管事项");
    expect(container.textContent).toContain("等待语义复审");

    const governance = container.querySelector(".fae-session-governance")?.textContent;
    expect(governance).toContain("数据截止时间");
    expect(governance).toContain("2026-07-21T09:10:00Z");
    expect(governance).toContain("DingTalk");
    expect(governance).toContain("resolved");
    expect(governance).toContain("身份信息暂不可用");
    expect(governance).not.toContain("ai-fae-agent");
  });

  it("loads closure summaries for all FAE Turns in batches of at most 200", async () => {
    const turns = Array.from({ length: 201 }, (_, index) => ({
      ...turnWithoutFeedback,
      turn_key: `fae:turn-${index + 1}`,
      turn_index: index + 1,
    }));
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path.startsWith("/api/admin/fae/sessions/")) {
        return Promise.resolve(response({ ...session, turn_count: turns.length, turns }));
      }
      if (path.startsWith("/api/review/turn-summaries?")) {
        requests.push(path);
        return Promise.resolve(response([]));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await act(async () => root.render(<FaeSessionDetailPage sessionKey={session.session_key} />));

    expect(requests).toHaveLength(2);
    expect(new URL(requests[0], "https://platform.test").searchParams.getAll("turn_key")).toHaveLength(200);
    expect(new URL(requests[1], "https://platform.test").searchParams.getAll("turn_key")).toHaveLength(1);
  });

  it("aborts obsolete Session loading and ignores its late result", async () => {
    let resolveFirst!: (value: Response) => void;
    let firstSignal: AbortSignal | undefined;
    const firstResponse = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    const secondSession = {
      ...session,
      session_key: "fae:session-2",
      title: "Current scoped Session",
      turns: [],
    };
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("fae%3Asession-1")) {
        firstSignal = init?.signal ?? undefined;
        return firstResponse;
      }
      if (path.endsWith("fae%3Asession-2")) return Promise.resolve(response(secondSession));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await act(async () => root.render(<FaeSessionDetailPage sessionKey={session.session_key} />));
    await act(async () => root.render(<FaeSessionDetailPage sessionKey={secondSession.session_key} />));
    expect(firstSignal?.aborted).toBe(true);

    await act(async () => resolveFirst(response({ ...session, title: "Obsolete Session" })));

    expect(container.textContent).toContain("Current scoped Session");
    expect(container.textContent).not.toContain("Obsolete Session");
  });

  it("does not render a loaded Session under a newly pending Session URL", async () => {
    const loadedSession = { ...session, title: "Loaded Session A", turns: [] };
    const pendingSessionKey = "fae:session-pending";
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith(encodeURIComponent(loadedSession.session_key))) {
        return Promise.resolve(response(loadedSession));
      }
      if (path.endsWith(encodeURIComponent(pendingSessionKey))) return new Promise<Response>(() => undefined);
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await act(async () => root.render(<FaeSessionDetailPage sessionKey={loadedSession.session_key} />));
    expect(container.textContent).toContain("Loaded Session A");

    await act(async () => root.render(<FaeSessionDetailPage sessionKey={pendingSessionKey} />));

    expect(container.textContent).toContain("正在加载 Session");
    expect(container.textContent).not.toContain("Loaded Session A");
  });

  it("recovers from a failed Session when a new Session loads successfully", async () => {
    const failedSessionKey = "fae:session-failed";
    const recoveredSession = {
      ...session,
      session_key: "fae:session-recovered",
      title: "Recovered Session",
      turns: [],
    };
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith(encodeURIComponent(failedSessionKey))) return Promise.reject(new Error("unavailable"));
      if (path.endsWith(encodeURIComponent(recoveredSession.session_key))) {
        return Promise.resolve(response(recoveredSession));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await act(async () => root.render(<FaeSessionDetailPage sessionKey={failedSessionKey} />));
    expect(container.textContent).toContain("数据暂不可用");

    await act(async () => root.render(<FaeSessionDetailPage sessionKey={recoveredSession.session_key} />));

    expect(container.textContent).toContain("Recovered Session");
    expect(container.textContent).not.toContain("数据暂不可用");
  });
});
