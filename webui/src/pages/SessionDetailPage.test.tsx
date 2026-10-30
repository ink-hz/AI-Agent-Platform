/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionDetail, TurnDetail } from "../types";
import { SessionDetailPage } from "./SessionDetailPage";


const session: SessionDetail = {
  session_key: "fae:session-1",
  agent_id: "ai-fae-agent",
  source_kind: "fae",
  channel: "DingTalk",
  title: "Gemini 335L troubleshooting",
  created_at: "2026-07-21T08:00:00Z",
  last_active_at: "2026-07-21T09:00:00Z",
  turn_count: 0,
  feedback_count: 0,
  review_count: 0,
  latest_outcome: null,
  source_synced_at: "2026-07-21T09:10:00Z",
  freshness: "fresh",
  participant_count: null,
  primary_sender_name: null,
  primary_sender_department: null,
  sender_identity_status: "unavailable",
  turns: [],
};

const negativeTurn: TurnDetail = {
  turn_key: "fae:turn-1", session_key: session.session_key, agent_id: session.agent_id,
  source_kind: "fae", turn_index: 1, question: "问题", answer: "旧答案",
  created_at: "2026-08-03T00:00:00Z", question_at: null, answer_at: null,
  question_time_status: "unavailable", answer_time_status: "unavailable",
  trace_key: null, outcome: "resolved", fallback_used: false, duration_ms: 1,
  sources: [], evidence: [], evidence_availability: "available",
  feedback: [{ feedback_key: "fae:feedback-1", sentiment: "negative", raw_rating: "bad", reason_code: null, comment: "错误", created_at: "2026-08-03T00:00:00Z", details: {} }],
  reviews: [], improvements: [], input_attachments: [], output_attachments: [],
  details: {}, sender_name: null, sender_department: null,
  sender_identity_status: "unavailable",
};

const ordinaryTurn: TurnDetail = {
  ...negativeTurn,
  turn_key: "fae:turn-2",
  turn_index: 2,
  feedback: [],
};


function response<T>(body: T): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}


describe("SessionDetailPage return navigation", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    window.history.replaceState({}, "", "/admin/sessions/fae%3Asession-1");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(session))));
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  async function renderPage() {
    await act(async () => root.render(<SessionDetailPage sessionKey={session.session_key} />));
  }

  it("refreshes visible replay, retains data on failure, stops hidden and cancels stale responses", async () => {
    vi.useFakeTimers();
    let hidden = false;
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
    const fetchMock = vi.fn().mockResolvedValueOnce(response(session))
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(response({ ...session, title: "新标题" }));
    vi.stubGlobal("fetch", fetchMock);
    await renderPage();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain(session.title);
    expect(container.textContent).toContain("刷新失败");
    expect(container.querySelector(".freshness-stale")).toBeNull();
    hidden = true;
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    hidden = false;
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(container.textContent).toContain("新标题");
    expect(container.textContent).not.toContain("刷新失败");
    const signal = fetchMock.mock.calls[2][1].signal as AbortSignal;
    await act(async () => root.unmount());
    expect(signal.aborted).toBe(true);
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    root = createRoot(container);
  });

  it("ignores a late response after the page was hidden and resumed", async () => {
    vi.useFakeTimers();
    let hidden = false;
    let finishOldRequest: (value: Response) => void = () => undefined;
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
    const fetchMock = vi.fn().mockResolvedValueOnce(response(session))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishOldRequest = resolve; }))
      .mockResolvedValueOnce(response({ ...session, title: "当前回放" }));
    vi.stubGlobal("fetch", fetchMock);
    await renderPage();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    const oldSignal = fetchMock.mock.calls[1][1].signal as AbortSignal;
    hidden = true;
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(oldSignal.aborted).toBe(true);
    hidden = false;
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => finishOldRequest(response({ ...session, title: "迟到的旧回放" })));
    expect(container.textContent).toContain("当前回放");
    expect(container.textContent).not.toContain("迟到的旧回放");
  });

  it("returns to the validated true source", async () => {
    window.history.replaceState({
      sessionOrigin: { path: "/admin/agents/ai-fae-agent", scrollY: 500 },
    }, "", "/admin/sessions/fae%3Asession-1");
    const back = vi.spyOn(window.history, "back").mockImplementation(() => undefined);
    await renderPage();

    const link = container.querySelector<HTMLAnchorElement>(".back-link")!;
    expect(link.textContent).toBe("← 返回");
    expect(link.getAttribute("href")).toBe("/admin/agents/ai-fae-agent");
    await act(async () => link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
    expect(back).toHaveBeenCalledOnce();
  });

  it("falls back to All Sessions for a direct entry", async () => {
    await renderPage();

    const link = container.querySelector<HTMLAnchorElement>(".back-link")!;
    expect(link.textContent).toBe("← 返回 Session 列表");
    expect(link.getAttribute("href")).toBe("/admin/sessions");
    expect(container.textContent).toContain("Session 回放");
    expect(container.textContent).toContain("Gemini 335L troubleshooting");
    expect(container.textContent).not.toContain("SESSION REPLAY");
  });

  it("loads closure summaries once for all negative turns", async () => {
    const fetchMock = vi.fn((path: string) => {
      if (path.startsWith("/api/sessions/")) {
        return Promise.resolve(response({ ...session, turn_count: 1, turns: [negativeTurn] }));
      }
      if (path.startsWith("/api/review/turn-summaries?")) {
        return Promise.resolve(response([{
          turn_key: negativeTurn.turn_key,
          issue_id: "issue-1",
          status: "awaiting_review",
          missing_gates: ["semantic_review"],
          latest_valid_replay_id: "replay-1",
        }]));
      }
      throw new Error(`unexpected path ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderPage();
    await act(async () => await Promise.resolve());

    expect(fetchMock.mock.calls.filter(([path]) => String(path).startsWith("/api/review/turn-summaries?")).length).toBe(1);
    expect(container.textContent).toContain("等待语义复审");
    expect(container.textContent).toContain("缺少：独立语义复审");
  });

  it("keeps generic replay negative-only with generic review links", async () => {
    let summaryPath = "";
    vi.stubGlobal("fetch", vi.fn((path: string) => {
      if (path.startsWith("/api/sessions/")) {
        return Promise.resolve(response({
          ...session,
          turn_count: 2,
          turns: [ordinaryTurn, { ...negativeTurn, turn_key: "fae:turn-3" }],
        }));
      }
      if (path.startsWith("/api/review/turn-summaries?")) {
        summaryPath = path;
        return Promise.resolve(response([]));
      }
      throw new Error(`unexpected path ${path}`);
    }));

    await renderPage();

    const actions = [...container.querySelectorAll<HTMLAnchorElement>(".review-entry a")];
    expect(actions).toHaveLength(1);
    expect(actions[0].getAttribute("href")).toBe(
      "/admin/review?agent_id=ai-fae-agent&turn_key=fae%3Aturn-3",
    );
    expect(new URL(summaryPath, "https://platform.test").searchParams.getAll("turn_key")).toEqual(["fae:turn-3"]);
    expect(container.textContent).not.toContain("创建或查看问题");
  });
});
