/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Page, SessionSummary } from "../types";
import { FaeSessionsPage } from "./FaeSessionsPage";


function response<T>(body: T): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}


function setInput(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}


const session: SessionSummary = {
  session_key: "fae:session-1",
  agent_id: "ai-fae-agent",
  source_kind: "fae",
  channel: "fae",
  title: "反馈待复核",
  created_at: "2026-08-17T08:00:00Z",
  last_active_at: "2026-08-17T09:00:00Z",
  turn_count: 2,
  feedback_count: 1,
  review_count: 0,
  latest_outcome: "failed",
  source_synced_at: "2026-08-17T09:05:00Z",
  freshness: "fresh",
  participant_count: 1,
  primary_sender_name: null,
  primary_sender_department: null,
  sender_identity_status: "unavailable",
};


describe("FaeSessionsPage", () => {
  let container: HTMLDivElement;
  let root: Root;
  let requests: string[];

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    requests = [];
    window.history.replaceState({}, "", "/admin/fae/sessions?channel=fae&sentiment=negative&page=2");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      requests.push(path);
      if (path === "/api/agents") return Promise.resolve(response([]));
      if (path.startsWith("/api/")) {
        return Promise.resolve(response<Page<SessionSummary>>({ items: [session], total: 51, limit: 50, offset: 50 }));
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

  it("uses the immutable FAE scope with URL-backed filters and detail links", async () => {
    await act(async () => root.render(<FaeSessionsPage />));

    expect(requests[requests.length - 1]).toBe(
      "/api/admin/fae/sessions?channel=fae&sentiment=negative&limit=50&offset=50",
    );
    expect(container.querySelector('select[name="agent_id"]')).toBeNull();
    expect(container.querySelector('select[name="source_kind"]')).toBeNull();
    expect(container.querySelector('input[name="q"]')).not.toBeNull();
    expect(container.querySelector('input[name="channel"]')).not.toBeNull();
    expect(container.querySelector('select[name="sentiment"]')).not.toBeNull();
    expect(container.querySelector('input[name="review_status"]')).not.toBeNull();
    expect(container.querySelector('input[name="outcome"]')).not.toBeNull();
    expect(container.querySelector('input[name="date_from"]')).not.toBeNull();
    expect(container.querySelector('input[name="date_to"]')).not.toBeNull();
    expect(container.querySelector('a.session-row')?.getAttribute("href")).toBe(
      "/admin/fae/sessions/fae%3Asession-1",
    );
  });

  it("removes generic scope filters from an FAE Session URL", async () => {
    window.history.replaceState({}, "", "/admin/fae/sessions?agent_id=other-agent&source_kind=admin&channel=fae");

    await act(async () => root.render(<FaeSessionsPage />));

    expect(`${window.location.pathname}${window.location.search}`).toBe("/admin/fae/sessions?channel=fae");
    expect(requests[requests.length - 1]).toBe("/api/admin/fae/sessions?channel=fae&limit=50");
  });

  it("keeps timezone-bearing date filters visible and canonical", async () => {
    window.history.replaceState({}, "", "/admin/fae/sessions?date_from=2026-08-01T00%3A00%3A00%2B08%3A00&date_to=2026-08-31T23%3A59%3A59%2B08%3A00");

    await act(async () => root.render(<FaeSessionsPage />));

    expect(container.querySelector<HTMLInputElement>('input[name="date_from"]')?.value).toBe("2026-08-01T00:00:00+08:00");
    expect(container.querySelector<HTMLInputElement>('input[name="date_to"]')?.value).toBe("2026-08-31T23:59:59+08:00");
    expect(requests[requests.length - 1]).toBe("/api/admin/fae/sessions?date_from=2026-08-01T00%3A00%3A00%2B08%3A00&date_to=2026-08-31T23%3A59%3A59%2B08%3A00&limit=50");
  });

  it("round-trips the overview exclusive period end without converting it to inclusive", async () => {
    window.history.replaceState({}, "", "/admin/fae/sessions?date_from=2026-08-24T00%3A00%3A00%2B08%3A00&date_before=2026-08-31T00%3A00%3A00%2B08%3A00");

    await act(async () => root.render(<FaeSessionsPage />));

    expect(`${window.location.pathname}${window.location.search}`).toContain("date_before=2026-08-31T00%3A00%3A00%2B08%3A00");
    expect(requests[requests.length - 1]).toBe("/api/admin/fae/sessions?date_from=2026-08-24T00%3A00%3A00%2B08%3A00&date_before=2026-08-31T00%3A00%3A00%2B08%3A00&limit=50");
  });

  it.each([
    "not-a-date",
    "2026-08-01T00:00:00",
    "2026-02-30T00:00:00+08:00",
  ])("canonicalizes invalid FAE date_from %s before loading", async (dateFrom) => {
    window.history.replaceState({}, "", `/admin/fae/sessions?date_from=${encodeURIComponent(dateFrom)}`);

    await act(async () => root.render(<FaeSessionsPage />));

    expect(`${window.location.pathname}${window.location.search}`).toBe("/admin/fae/sessions");
    expect(requests[requests.length - 1]).toBe("/api/admin/fae/sessions?limit=50");
  });

  it("does not serialize an invalid FAE date entered in the filter form", async () => {
    window.history.replaceState({}, "", "/admin/fae/sessions");
    await act(async () => root.render(<FaeSessionsPage />));

    await act(async () => {
      setInput(container.querySelector<HTMLInputElement>('input[name="date_from"]')!, "not-a-date");
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(`${window.location.pathname}${window.location.search}`).toBe("/admin/fae/sessions");
    expect(requests[requests.length - 1]).toBe("/api/admin/fae/sessions?limit=50");
  });
});
