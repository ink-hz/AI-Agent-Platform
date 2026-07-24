/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionDetail } from "../types";
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
    window.history.replaceState({}, "", "/sessions/fae%3Asession-1");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(session))));
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function renderPage() {
    await act(async () => root.render(<SessionDetailPage sessionKey={session.session_key} />));
  }

  it("returns to the validated true source", async () => {
    window.history.replaceState({
      sessionOrigin: { path: "/agents/ai-fae-agent", scrollY: 500 },
    }, "", "/sessions/fae%3Asession-1");
    const back = vi.spyOn(window.history, "back").mockImplementation(() => undefined);
    await renderPage();

    const link = container.querySelector<HTMLAnchorElement>(".back-link")!;
    expect(link.textContent).toBe("← 返回");
    expect(link.getAttribute("href")).toBe("/agents/ai-fae-agent");
    await act(async () => link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
    expect(back).toHaveBeenCalledOnce();
  });

  it("falls back to All Sessions for a direct entry", async () => {
    await renderPage();

    const link = container.querySelector<HTMLAnchorElement>(".back-link")!;
    expect(link.textContent).toBe("← 返回 Session 列表");
    expect(link.getAttribute("href")).toBe("/sessions");
    expect(container.textContent).toContain("Session 回放");
    expect(container.textContent).toContain("Gemini 335L troubleshooting");
    expect(container.textContent).not.toContain("SESSION REPLAY");
  });
});
