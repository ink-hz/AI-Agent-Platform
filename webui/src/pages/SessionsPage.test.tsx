/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentSummary, Page, SessionSummary } from "../types";
import { SessionsPage } from "./SessionsPage";


const agents: AgentSummary[] = [{
  id: "ai-fae-agent",
  name: "AI FAE",
  domain: "Field Application Engineering",
  description: "Production engineering Agent",
  glyph: "FAE",
  accent: "cyan",
  visibility: "business",
  source_kind: "fae",
  deployment: "Alibaba Cloud",
  session_count: 1,
  total_turns: 2,
  last_activity_at: "2026-07-21T09:00:00Z",
  last_synced_at: "2026-07-21T09:10:00Z",
  freshness: "fresh",
}];


function response<T>(body: T): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}


const sessionFixture: SessionSummary = {
  session_key: "ai-fae-agent:session-1",
  agent_id: "ai-fae-agent",
  source_kind: "fae",
  channel: "web",
  title: "分页验证 Session",
  created_at: "2026-08-17T08:00:00Z",
  last_active_at: "2026-08-17T09:00:00Z",
  turn_count: 2,
  feedback_count: 0,
  review_count: 0,
  latest_outcome: null,
  source_synced_at: "2026-08-17T09:05:00Z",
  freshness: "fresh",
  participant_count: 1,
  primary_sender_name: null,
  primary_sender_department: null,
  sender_identity_status: "unavailable",
};


function sessionPage(total = 0, offset = 0, count = 0): Page<SessionSummary> {
  return {
    items: Array.from({ length: count }, (_, index) => ({
      ...sessionFixture,
      session_key: `ai-fae-agent:session-${offset + index + 1}`,
    })),
    total,
    limit: 50,
    offset,
  };
}


function setInput(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}


function setSelect(select: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}


describe("SessionsPage URL state", () => {
  let container: HTMLDivElement;
  let root: Root;
  let sessionPaths: string[];
  let sessionResult: (path: string) => Page<SessionSummary>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    sessionPaths = [];
    sessionResult = () => sessionPage();
    window.history.replaceState({}, "", "/admin/sessions");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/agents") return Promise.resolve(response(agents));
      if (path.startsWith("/api/sessions")) {
        sessionPaths.push(path);
        return Promise.resolve(response(sessionResult(path)));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function renderPage() {
    await act(async () => root.render(<SessionsPage />));
  }

  it("hydrates filters from the URL and requests the same result", async () => {
    window.history.replaceState({}, "", "/admin/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini");

    await renderPage();

    expect(container.querySelector<HTMLSelectElement>('select[name="agent_id"]')?.value).toBe("ai-fae-agent");
    expect(container.querySelector<HTMLSelectElement>('select[name="source_kind"]')?.value).toBe("fae");
    expect(container.querySelector<HTMLInputElement>('input[name="q"]')?.value).toBe("Gemini");
    expect(sessionPaths).toContain("/api/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini&limit=50");
    expect(container.querySelector("h1")?.textContent).toBe("Session");
    expect(container.textContent).toContain("查看各 Agent 的真实 Session 和对话记录");
    expect(container.querySelector('option[value=""]')?.textContent).toBe("全部业务 Agent");
    expect(container.querySelector('button[type="submit"]')?.textContent).toBe("搜索");
    expect(container.textContent).not.toContain("CONVERSATION RECORD");
  });

  it("replaces the URL when filters are applied", async () => {
    await renderPage();

    await act(async () => {
      setSelect(container.querySelector<HTMLSelectElement>('select[name="agent_id"]')!, "ai-fae-agent");
      setSelect(container.querySelector<HTMLSelectElement>('select[name="source_kind"]')!, "fae");
      setInput(container.querySelector<HTMLInputElement>('input[name="q"]')!, " Gemini 335L ");
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(`${window.location.pathname}${window.location.search}`).toBe(
      "/admin/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini+335L",
    );
    expect(sessionPaths[sessionPaths.length - 1]).toBe(
      "/api/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini+335L&limit=50",
    );
  });

  it("restores controls and requests when browser history changes", async () => {
    await renderPage();
    window.history.pushState({}, "", "/admin/sessions?agent_id=ai-fae-agent&source_kind=fae&q=restored");

    await act(async () => window.dispatchEvent(new PopStateEvent("popstate")));

    expect(container.querySelector<HTMLSelectElement>('select[name="agent_id"]')?.value).toBe("ai-fae-agent");
    expect(container.querySelector<HTMLSelectElement>('select[name="source_kind"]')?.value).toBe("fae");
    expect(container.querySelector<HTMLInputElement>('input[name="q"]')?.value).toBe("restored");
    expect(sessionPaths[sessionPaths.length - 1]).toBe(
      "/api/sessions?agent_id=ai-fae-agent&source_kind=fae&q=restored&limit=50",
    );
  });

  it("requests the selected offset and renders the authorized result range", async () => {
    window.history.replaceState({}, "", "/admin/sessions?agent_id=ai-fae-agent&page=3");
    sessionResult = () => sessionPage(123, 100, 23);

    await renderPage();

    expect(sessionPaths).toContain("/api/sessions?agent_id=ai-fae-agent&limit=50&offset=100");
    expect(container.textContent).toContain("第 101–123 条，共 123 条");
    expect(container.textContent).toContain("第 3 / 3 页");
    expect(Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "下一页")?.disabled).toBe(true);
  });

  it("moves to the next page through URL history and requests its offset", async () => {
    sessionResult = (path) => path.includes("offset=50")
      ? sessionPage(120, 50, 50)
      : sessionPage(120, 0, 50);
    await renderPage();

    const next = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "下一页");
    expect(next).toBeDefined();
    await act(async () => next?.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    expect(`${window.location.pathname}${window.location.search}`).toBe("/admin/sessions?page=2");
    expect(sessionPaths[sessionPaths.length - 1]).toBe("/api/sessions?limit=50&offset=50");
  });

  it("resets to page one when a filter changes", async () => {
    window.history.replaceState({}, "", "/admin/sessions?source_kind=fae&page=3");
    sessionResult = () => sessionPage(120, 100, 20);
    await renderPage();

    await act(async () => {
      setSelect(container.querySelector<HTMLSelectElement>('select[name="agent_id"]')!, "ai-fae-agent");
    });

    expect(`${window.location.pathname}${window.location.search}`).toBe(
      "/admin/sessions?agent_id=ai-fae-agent&source_kind=fae",
    );
    expect(sessionPaths[sessionPaths.length - 1]).toBe(
      "/api/sessions?agent_id=ai-fae-agent&source_kind=fae&limit=50",
    );
  });

  it("replaces an out-of-range page with the last valid page", async () => {
    window.history.replaceState({}, "", "/admin/sessions?page=9");
    sessionResult = (path) => path.includes("offset=400")
      ? sessionPage(120, 400, 0)
      : sessionPage(120, 100, 20);

    await renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(`${window.location.pathname}${window.location.search}`).toBe("/admin/sessions?page=3");
    expect(sessionPaths).toContain("/api/sessions?limit=50&offset=400");
    expect(sessionPaths[sessionPaths.length - 1]).toBe("/api/sessions?limit=50&offset=100");
  });
});
