/** @vitest-environment jsdom */

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { DailyBrief } from "./components/DailyBrief";
import { OperationalEventItem } from "./components/OperationalEventItem";
import { ActivityPage } from "./pages/ActivityPage";
import type { AgentSummary, OperationalEvent, OperationsBrief, Page } from "./types";


const briefFixture: OperationsBrief = {
  period_start: "2026-07-21T10:00:00Z",
  period_end: "2026-07-22T10:00:00Z",
  freshness: { status: "current", evaluated_at: "2026-07-22T10:00:00Z", failed_groups: [] },
  can_claim_healthy: false,
  attention: [{
    event_id: "runtime:ai-fae-agent",
    agent_id: "ai-fae-agent",
    agent_visibility: "business",
    event_type: "runtime_offline",
    event_family: "runtime",
    severity: "critical",
    status: "active",
    title: "AI FAE Agent is offline",
    summary: "Two consecutive runtime observations",
    source_kind: "runtime",
    occurred_at: "2026-07-22T09:30:00Z",
    first_observed_at: "2026-07-22T09:25:00Z",
    last_observed_at: "2026-07-22T09:30:00Z",
    resolved_at: null,
    facts: { consecutive_observations: 2 },
    target_kind: "agent",
    target_id: "ai-fae-agent",
    target_path: "/agents/ai-fae-agent",
    fingerprint: "runtime:ai-fae-agent:offline",
  }],
  usage: {
    conversations: 33,
    active_agents: 6,
    leaders: [{ agent_id: "ai-fae-agent", agent_name: "AI FAE Agent", conversations: 12 }],
  },
  changes: [{
    event_id: "recovery:ai-fae-agent",
    agent_id: "ai-fae-agent",
    agent_visibility: "business",
    event_type: "runtime_recovered",
    event_family: "recovery",
    severity: "info",
    status: "historical",
    title: "AI FAE Agent recovered",
    summary: "Runtime observations returned to normal",
    source_kind: "runtime",
    occurred_at: "2026-07-22T08:30:00Z",
    first_observed_at: "2026-07-22T08:30:00Z",
    last_observed_at: "2026-07-22T08:30:00Z",
    resolved_at: "2026-07-22T08:30:00Z",
    facts: {},
    target_kind: "agent",
    target_id: "ai-fae-agent",
    target_path: "/agents/ai-fae-agent",
    fingerprint: "recovery:ai-fae-agent:runtime",
  }],
};

const partialFreshness: OperationsBrief["freshness"] = {
  status: "partial",
  evaluated_at: "2026-07-22T10:00:00Z",
  failed_groups: ["execution"],
};

const agents: AgentSummary[] = [{
  id: "hr-bot",
  name: "招聘助手",
  domain: "招聘",
  description: "招聘流程助手",
  glyph: "HR",
  accent: "blue",
  visibility: "business",
  source_kind: "metabot",
  deployment: "local",
  session_count: 4,
  total_turns: 8,
  last_activity_at: "2026-07-22T16:05:00Z",
  last_synced_at: "2026-07-22T16:05:00Z",
  freshness: "live",
}, {
  id: "test-bot",
  name: "测试机器人",
  domain: "平台测试",
  description: "系统测试代理",
  glyph: "TB",
  accent: "slate",
  visibility: "system",
  source_kind: "metabot",
  deployment: "local",
  session_count: 1,
  total_turns: 1,
  last_activity_at: "2026-07-22T16:05:00Z",
  last_synced_at: "2026-07-22T16:05:00Z",
  freshness: "live",
}];

function eventFixture(
  eventId: string,
  title: string,
  occurredAt: string,
  overrides: Partial<OperationalEvent> = {},
): OperationalEvent {
  return {
    ...briefFixture.changes[0],
    event_id: eventId,
    title,
    occurred_at: occurredAt,
    first_observed_at: occurredAt,
    last_observed_at: occurredAt,
    fingerprint: eventId,
    ...overrides,
  };
}

function response<T>(body: T): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve };
}

function setInput(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function setSelect(select: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}


describe("DailyBrief", () => {
  it("renders evidence-backed Attention and Last 24 Hours", () => {
    const html = renderToStaticMarkup(<DailyBrief brief={briefFixture} />);
    expect(html).toContain("Needs Attention");
    expect(html).toContain("Last 24 Hours");
    expect(html).toContain("AI FAE Agent is offline");
    expect(html).toContain("Two consecutive runtime observations");
    expect(html).toContain("View all activity");
  });

  it("does not claim health for partial evaluation", () => {
    const html = renderToStaticMarkup(
      <DailyBrief brief={{ ...briefFixture, freshness: partialFreshness, can_claim_healthy: false, attention: [] }} />,
    );
    expect(html).not.toContain("No critical issues");
    expect(html).toContain("Brief partially evaluated");
  });

  it("does not claim health when last-known data is locally stale", () => {
    const html = renderToStaticMarkup(
      <DailyBrief brief={{ ...briefFixture, can_claim_healthy: true, attention: [] }} stale />,
    );
    expect(html).not.toContain("No critical issues");
    expect(html).toContain("Brief data is stale");
  });

  it("does not present resolved events as active Attention", () => {
    const html = renderToStaticMarkup(
      <DailyBrief brief={{
        ...briefFixture,
        can_claim_healthy: true,
        attention: [{ ...briefFixture.attention[0], status: "resolved" }],
      }} />,
    );

    expect(html).not.toContain("AI FAE Agent is offline");
    expect(html).toContain("No critical issues");
  });

  it("limits Overview changes to five and summarizes fleet usage", () => {
    const changes = Array.from({ length: 6 }, (_, index) => ({
      ...briefFixture.changes[0],
      event_id: `change-${index}`,
      title: `Change ${index + 1}`,
    }));
    const html = renderToStaticMarkup(<DailyBrief brief={{ ...briefFixture, changes }} />);

    expect(html).toContain("33 new conversations across 6 Business Agents");
    expect(html).toContain("Change 5");
    expect(html).not.toContain("Change 6");
  });

  it("renders severity with an icon and readable text", () => {
    const html = renderToStaticMarkup(<OperationalEventItem event={briefFixture.attention[0]} />);

    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("Critical");
    expect(html).toContain('href="/agents/ai-fae-agent"');
  });
});


describe("ActivityPage", () => {
  let container: HTMLDivElement;
  let root: Root | null;

  beforeEach(() => {
    vi.setSystemTime(new Date("2026-07-22T16:30:00Z"));
    window.history.replaceState({}, "", "/activity");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    if (root !== null) await act(async () => root?.unmount());
    container.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function renderActivity() {
    await act(async () => root?.render(createElement(ActivityPage)));
  }

  it("renders the five filters, Business selector defaults, and Shanghai date groups", async () => {
    const items = [
      eventFixture("today", "Today event", "2026-07-22T16:05:00Z"),
      eventFixture("yesterday", "Yesterday event", "2026-07-22T15:55:00Z"),
      eventFixture("older", "Older event", "2026-07-21T15:55:00Z"),
    ];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/agents") return Promise.resolve(response(agents));
      return Promise.resolve(response<Page<OperationalEvent>>({ items, total: 3, limit: 50, offset: 0 }));
    }));

    await renderActivity();

    expect(Array.from(container.querySelectorAll(".filter-bar label > span"), (node) => node.textContent))
      .toEqual(["Agent", "Event type", "Severity", "From", "To"]);
    const options = Array.from(container.querySelectorAll("select[name=agent_id] option"), (node) => node.textContent);
    expect(options).toEqual(["All Business Agents", "招聘助手"]);
    expect(options).not.toContain("测试机器人");
    expect(Array.from(container.querySelectorAll(".activity-group h2"), (node) => node.textContent))
      .toEqual(["Today", "Yesterday", "21 Jul 2026"]);
  });

  it("keeps an explicitly deep-linked System Agent selectable", async () => {
    window.history.replaceState({}, "", "/activity?agent_id=test-bot");
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/agents") return Promise.resolve(response(agents));
      return Promise.resolve(response<Page<OperationalEvent>>({ items: [], total: 0, limit: 50, offset: 0 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderActivity();

    const selector = container.querySelector<HTMLSelectElement>("select[name=agent_id]")!;
    expect(selector.value).toBe("test-bot");
    expect(Array.from(selector.options, (option) => option.textContent))
      .toEqual(["All Business Agents", "招聘助手", "测试机器人"]);
    const activityCall = fetchMock.mock.calls.find(([path]) => String(path).startsWith("/api/operations/events"))!;
    expect(activityCall[0]).toBe("/api/operations/events?agent_id=test-bot&limit=50&offset=0");
    expect(activityCall[1]?.signal).toBeInstanceOf(AbortSignal);
  });

  it("loads the next offset, de-duplicates in server order, and resets on filter submit", async () => {
    const first = eventFixture("first", "First event", "2026-07-22T16:10:00Z");
    const second = eventFixture("second", "Second event", "2026-07-22T16:00:00Z");
    const third = eventFixture("third", "Third event", "2026-07-22T15:50:00Z");
    const filtered = eventFixture("filtered", "Filtered event", "2026-07-22T16:20:00Z");
    const eventPaths: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/agents") return Promise.resolve(response(agents));
      eventPaths.push(path);
      if (path.includes("event_type=runtime_offline")) {
        return Promise.resolve(response<Page<OperationalEvent>>({ items: [filtered], total: 1, limit: 50, offset: 0 }));
      }
      if (path.includes("offset=2")) {
        return Promise.resolve(response<Page<OperationalEvent>>({ items: [second, third], total: 3, limit: 50, offset: 2 }));
      }
      return Promise.resolve(response<Page<OperationalEvent>>({ items: [first, second], total: 3, limit: 50, offset: 0 }));
    }));

    await renderActivity();
    await act(async () => container.querySelector<HTMLButtonElement>("button[type=button]")!.click());

    expect(eventPaths[1]).toBe("/api/operations/events?limit=50&offset=2");
    expect(Array.from(container.querySelectorAll(".operational-event-title"), (node) => node.textContent))
      .toEqual(["First event", "Second event", "Third event"]);

    await act(async () => {
      setInput(container.querySelector<HTMLInputElement>("input[name=event_type]")!, "runtime_offline");
      setSelect(container.querySelector<HTMLSelectElement>("select[name=severity]")!, "critical");
    });
    await act(async () => {
      container.querySelector<HTMLFormElement>("form")!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(eventPaths[2]).toBe("/api/operations/events?event_type=runtime_offline&severity=critical&limit=50&offset=0");
    expect(Array.from(container.querySelectorAll(".operational-event-title"), (node) => node.textContent))
      .toEqual(["Filtered event"]);
  });

  it("contains Activity unavailability inside the application shell", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      if (String(input) === "/api/agents") return Promise.resolve(response(agents));
      return Promise.reject(new Error("operations unavailable"));
    }));

    await act(async () => root?.render(createElement(App)));

    expect(container.querySelector(".topbar")).not.toBeNull();
    expect(container.querySelector(".readonly-tag")?.textContent).toContain("Read-only");
    expect(container.querySelector("h1")?.textContent).toBe("Activity History");
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Activity unavailable");
    expect(container.querySelector(".product-nav [aria-current=page]")).toBeNull();
  });

  it("aborts Agent and Activity requests on cleanup and ignores late results", async () => {
    const agentRequest = deferred<Response>();
    const activityRequest = deferred<Response>();
    const signals: AbortSignal[] = [];
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      signals.push(init?.signal as AbortSignal);
      return String(input) === "/api/agents" ? agentRequest.promise : activityRequest.promise;
    }));

    await renderActivity();
    await act(async () => root?.unmount());
    root = null;

    expect(signals).toHaveLength(2);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    await act(async () => {
      agentRequest.resolve(response(agents));
      activityRequest.resolve(response<Page<OperationalEvent>>({ items: [], total: 0, limit: 50, offset: 0 }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toBe("");
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
