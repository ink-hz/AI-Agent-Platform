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


function sessionPage(): Page<SessionSummary> {
  return { items: [], total: 0, limit: 50, offset: 0 };
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

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    sessionPaths = [];
    window.history.replaceState({}, "", "/sessions");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/agents") return Promise.resolve(response(agents));
      if (path.startsWith("/api/sessions")) {
        sessionPaths.push(path);
        return Promise.resolve(response(sessionPage()));
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
    window.history.replaceState({}, "", "/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini");

    await renderPage();

    expect(container.querySelector<HTMLSelectElement>('select[name="agent_id"]')?.value).toBe("ai-fae-agent");
    expect(container.querySelector<HTMLSelectElement>('select[name="source_kind"]')?.value).toBe("fae");
    expect(container.querySelector<HTMLInputElement>('input[name="q"]')?.value).toBe("Gemini");
    expect(sessionPaths).toContain("/api/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini&limit=50");
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
      "/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini+335L",
    );
    expect(sessionPaths[sessionPaths.length - 1]).toBe(
      "/api/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini+335L&limit=50",
    );
  });

  it("restores controls and requests when browser history changes", async () => {
    await renderPage();
    window.history.pushState({}, "", "/sessions?agent_id=ai-fae-agent&source_kind=fae&q=restored");

    await act(async () => window.dispatchEvent(new PopStateEvent("popstate")));

    expect(container.querySelector<HTMLSelectElement>('select[name="agent_id"]')?.value).toBe("ai-fae-agent");
    expect(container.querySelector<HTMLSelectElement>('select[name="source_kind"]')?.value).toBe("fae");
    expect(container.querySelector<HTMLInputElement>('input[name="q"]')?.value).toBe("restored");
    expect(sessionPaths[sessionPaths.length - 1]).toBe(
      "/api/sessions?agent_id=ai-fae-agent&source_kind=fae&q=restored&limit=50",
    );
  });
});
