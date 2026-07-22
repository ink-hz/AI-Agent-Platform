/** @vitest-environment jsdom */

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AgentSummary,
  FleetOverview,
  OperationalEvent,
  Page,
  SessionSummary,
} from "../types";
import { AgentDetailPage } from "./AgentDetailPage";


const agentFixture: AgentSummary = {
  id: "test-bot",
  name: "测试机器人",
  domain: "平台测试",
  description: "系统测试代理",
  glyph: "TB",
  accent: "testing",
  visibility: "system",
  source_kind: "metabot",
  deployment: "local",
  session_count: 1,
  total_turns: 2,
  last_activity_at: "2026-07-22T16:05:00Z",
  last_synced_at: "2026-07-22T16:05:00Z",
  freshness: "live",
};

const sessionFixture: SessionSummary = {
  session_key: "test-bot:session-1",
  agent_id: agentFixture.id,
  source_kind: "metabot",
  channel: "web",
  title: "System verification session",
  created_at: "2026-07-22T15:00:00Z",
  last_active_at: "2026-07-22T16:00:00Z",
  turn_count: 2,
  feedback_count: 0,
  review_count: 0,
  latest_outcome: null,
  source_synced_at: "2026-07-22T16:05:00Z",
  freshness: "live",
};

function fleetFixture(agent: AgentSummary): FleetOverview {
  return {
    summary: {
      total_agents: 1,
      running_agents: 1,
      active_agents: 1,
      degraded_agents: 0,
      offline_agents: 0,
      checking_agents: 0,
      total_conversations: 1,
      conversations_last_7d: 1,
      conversations_previous_7d: 0,
      change_percent: null,
    },
    trend: [],
    agents: [{
      id: agent.id,
      name: agent.name,
      domain: agent.domain,
      description: agent.description,
      glyph: agent.glyph,
      accent: agent.accent,
      visibility: agent.visibility,
      state: "active",
      live_since: "2026-07-21T10:00:00Z",
      live_since_basis: "release_artifact",
      last_updated_at: "2026-07-22T10:00:00Z",
      last_updated_basis: "repository_history",
      current_runtime_seconds: 3600,
      total_conversations: 1,
      conversations_last_7d: 1,
      last_activity_at: agent.last_activity_at,
      recent_summary: null,
    }],
    runtime_source: { healthy: true, checked_at: "2026-07-22T10:00:00Z", stale: false, error: null },
    usage_source: { healthy: true, checked_at: "2026-07-22T10:00:00Z", stale: false, error: null },
  };
}

function eventFixture(index: number, agentId = agentFixture.id): OperationalEvent {
  const occurredAt = `2026-07-22T${String(10 + index).padStart(2, "0")}:00:00Z`;
  return {
    event_id: `event-${agentId}-${index}`,
    agent_id: agentId,
    agent_visibility: agentFixture.visibility,
    event_type: "runtime_recovered",
    event_family: "recovery",
    severity: "info",
    status: "historical",
    title: `Operational change ${index}`,
    summary: `Evidence for change ${index}`,
    source_kind: "runtime",
    occurred_at: occurredAt,
    first_observed_at: occurredAt,
    last_observed_at: occurredAt,
    resolved_at: occurredAt,
    facts: {},
    target_kind: "agent",
    target_id: agentId,
    target_path: `/agents/${agentId}`,
    fingerprint: `event-${agentId}-${index}`,
  };
}

function response<T>(body: T): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((next, fail) => { resolve = next; reject = fail; });
  return { promise, reject, resolve };
}

function sessionsPage(agent: AgentSummary, items = [sessionFixture]): Page<SessionSummary> {
  return { items: items.map((item) => ({ ...item, agent_id: agent.id })), total: items.length, limit: 50, offset: 0 };
}

function activityPage(items: OperationalEvent[]): Page<OperationalEvent> {
  return { items, total: items.length, limit: 8, offset: 0 };
}

function settledFetch(agent: AgentSummary, events: OperationalEvent[] = []) {
  return vi.fn((input: string | URL | Request) => {
    const path = String(input);
    if (path.startsWith("/api/operations/events")) return Promise.resolve(response(activityPage(events)));
    if (path === "/api/fleet/overview") return Promise.resolve(response(fleetFixture(agent)));
    if (path.startsWith("/api/sessions?")) return Promise.resolve(response(sessionsPage(agent)));
    if (path.startsWith("/api/agents/")) return Promise.resolve(response(agent));
    return Promise.reject(new Error(`Unexpected request: ${path}`));
  });
}


describe("AgentDetailPage recent activity", () => {
  let container: HTMLDivElement;
  let root: Root | null;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    if (root !== null) await act(async () => root?.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function renderAgent(agentId = agentFixture.id) {
    await act(async () => root?.render(createElement(AgentDetailPage, { agentId })));
  }

  it("shows explicitly requested activity on a System Agent detail", async () => {
    const event = eventFixture(1);
    const fetchMock = settledFetch(agentFixture, [event]);
    vi.stubGlobal("fetch", fetchMock);

    await renderAgent();

    const activity = container.querySelector(".agent-activity-section")!;
    expect(activity.querySelector(".section-heading p")?.textContent).toBe("OPERATIONS HISTORY");
    expect(activity.querySelector("h2")?.textContent).toBe("Recent Activity");
    expect(activity.querySelector(".operational-event-title")?.textContent).toBe(event.title);
    expect(activity.querySelector("a[href='/activity?agent_id=test-bot']")?.textContent).toBe("View all activity →");
    expect(container.textContent).toContain("Live Since");
    expect(container.textContent).toContain("Last Updated");
    expect(container.textContent).toContain("Current Runtime");
    expect(fetchMock.mock.calls.filter(([path]) => String(path).startsWith("/api/operations/events")))
      .toEqual([["/api/operations/events?agent_id=test-bot&limit=8", expect.any(Object)]]);
  });

  it("retains activity that resolves before the profile request", async () => {
    const agentRequest = deferred<Response>();
    const event = eventFixture(1);
    const fetchMock = settledFetch(agentFixture, [event]);
    fetchMock.mockImplementation((input: string | URL | Request) => {
      if (String(input).startsWith("/api/agents/")) return agentRequest.promise;
      return settledFetch(agentFixture, [event])(input);
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderAgent();
    expect(container.textContent).toContain("Loading Agent profile");

    await act(async () => {
      agentRequest.resolve(response(agentFixture));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.querySelector(".agent-activity-section")?.textContent).toContain(event.title);
  });

  it("renders the profile and Sessions while the independent activity request is pending", async () => {
    const activityRequest = deferred<Response>();
    const fetchMock = settledFetch(agentFixture);
    fetchMock.mockImplementation((input: string | URL | Request) => {
      if (String(input).startsWith("/api/operations/events")) return activityRequest.promise;
      return settledFetch(agentFixture)(input);
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderAgent();

    expect(container.querySelector(".agent-profile h1")?.textContent).toBe(agentFixture.name);
    expect(container.textContent).toContain(sessionFixture.title);
    expect(container.querySelector(".agent-activity-section")?.textContent).toContain("Recent Activity");
    expect(container.querySelector(".agent-activity-section")?.textContent).toContain("Loading activity");
    expect(container.querySelector(".agent-activity-status")?.getAttribute("role")).toBe("status");
    expect(container.querySelector(".agent-activity-status")?.getAttribute("aria-live")).toBe("polite");
  });

  it("contains activity failure without replacing the Agent profile or Sessions", async () => {
    const fetchMock = settledFetch(agentFixture);
    fetchMock.mockImplementation((input: string | URL | Request) => {
      if (String(input).startsWith("/api/operations/events")) return Promise.reject(new Error("operations unavailable"));
      return settledFetch(agentFixture)(input);
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderAgent();

    expect(container.querySelector(".agent-profile h1")?.textContent).toBe(agentFixture.name);
    expect(container.textContent).toContain(sessionFixture.title);
    const alert = container.querySelector(".agent-activity-section [role=alert]");
    expect(alert?.textContent).toContain("Activity unavailable");
    expect(container.querySelector(":scope > [role=alert]")).toBeNull();
  });

  it("retains the existing page-level failure when Sessions are unavailable", async () => {
    const fetchMock = settledFetch(agentFixture, [eventFixture(1)]);
    fetchMock.mockImplementation((input: string | URL | Request) => {
      if (String(input).startsWith("/api/sessions?")) return Promise.reject(new Error("sessions unavailable"));
      return settledFetch(agentFixture, [eventFixture(1)])(input);
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderAgent();

    expect(container.querySelector(":scope > [role=alert]")?.textContent).toContain("Data unavailable");
    expect(container.querySelector(".agent-profile")).toBeNull();
  });

  it("shows the exact empty activity copy", async () => {
    vi.stubGlobal("fetch", settledFetch(agentFixture));

    await renderAgent();

    expect(container.querySelector(".agent-activity-section")?.textContent)
      .toContain("No operational changes recorded yet.");
    expect(container.querySelector(".agent-activity-status")?.getAttribute("role")).toBe("status");
    expect(container.querySelector(".agent-activity-status")?.getAttribute("aria-live")).toBe("polite");
  });

  it("renders at most eight events, encodes the activity link, and requests activity once", async () => {
    const encodedAgent = { ...agentFixture, id: "system/qa agent?" };
    const events = Array.from({ length: 10 }, (_, index) => eventFixture(index, encodedAgent.id));
    const fetchMock = settledFetch(encodedAgent, events);
    vi.stubGlobal("fetch", fetchMock);

    await renderAgent(encodedAgent.id);

    expect(container.querySelectorAll(".agent-activity-section .operational-event-item")).toHaveLength(8);
    expect(container.querySelector(".agent-activity-section a[href='/activity?agent_id=system%2Fqa%20agent%3F']"))
      .not.toBeNull();
    const activityCalls = fetchMock.mock.calls.filter(([path]) => String(path).startsWith("/api/operations/events"));
    expect(activityCalls).toHaveLength(1);
    expect(activityCalls[0][0]).toBe("/api/operations/events?agent_id=system%2Fqa+agent%3F&limit=8");
  });

  it("aborts activity on cleanup and suppresses a late response", async () => {
    const activityRequest = deferred<Response>();
    let activitySignal: AbortSignal | undefined;
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fetchMock = settledFetch(agentFixture);
    fetchMock.mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      if (String(input).startsWith("/api/operations/events")) {
        activitySignal = init?.signal as AbortSignal;
        return activityRequest.promise;
      }
      return settledFetch(agentFixture)(input);
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderAgent();
    await act(async () => root?.unmount());
    root = null;

    expect(activitySignal?.aborted).toBe(true);
    await act(async () => {
      activityRequest.resolve(response(activityPage([eventFixture(1)])));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.textContent).toBe("");
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("aborts old activity and ignores its late result when the Agent ID changes in place", async () => {
    const oldActivity = deferred<Response>();
    let oldSignal: AbortSignal | undefined;
    const nextAgent = { ...agentFixture, id: "next-bot", name: "Next Bot", visibility: "business" as const };
    const nextEvent = eventFixture(2, nextAgent.id);
    const oldEvent = eventFixture(1);
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/operations/events?agent_id=test-bot&limit=8") {
        oldSignal = init?.signal as AbortSignal;
        return oldActivity.promise;
      }
      const currentAgent = path.includes("next-bot") ? nextAgent : agentFixture;
      if (path.startsWith("/api/operations/events")) return Promise.resolve(response(activityPage([nextEvent])));
      if (path === "/api/fleet/overview") return Promise.resolve(response(fleetFixture(currentAgent)));
      if (path.startsWith("/api/sessions?")) return Promise.resolve(response(sessionsPage(currentAgent)));
      if (path.startsWith("/api/agents/")) return Promise.resolve(response(currentAgent));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await renderAgent();
    await renderAgent(nextAgent.id);

    expect(oldSignal?.aborted).toBe(true);
    expect(container.querySelector(".agent-activity-section")?.textContent).toContain(nextEvent.title);
    await act(async () => {
      oldActivity.resolve(response(activityPage([oldEvent])));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.querySelector(".agent-activity-section")?.textContent).toContain(nextEvent.title);
    expect(container.querySelector(".agent-activity-section")?.textContent).not.toContain(oldEvent.title);
  });

  it("does not show new Agent activity beneath a previous Agent profile during a route transition", async () => {
    const nextSessions = deferred<Response>();
    const nextAgent = { ...agentFixture, id: "next-bot", name: "Next Bot", visibility: "business" as const };
    const nextEvent = eventFixture(2, nextAgent.id);
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/operations/events?agent_id=test-bot&limit=8") {
        return Promise.resolve(response(activityPage([eventFixture(1)])));
      }
      if (path === "/api/operations/events?agent_id=next-bot&limit=8") {
        return Promise.resolve(response(activityPage([nextEvent])));
      }
      if (path === "/api/sessions?agent_id=next-bot&limit=50") return nextSessions.promise;
      if (path.startsWith("/api/sessions?")) return Promise.resolve(response(sessionsPage(agentFixture)));
      if (path === "/api/agents/next-bot") return Promise.resolve(response(nextAgent));
      if (path.startsWith("/api/agents/")) return Promise.resolve(response(agentFixture));
      if (path === "/api/fleet/overview") {
        const fleet = fleetFixture(agentFixture);
        fleet.agents.push(fleetFixture(nextAgent).agents[0]);
        return Promise.resolve(response(fleet));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await renderAgent();
    await renderAgent(nextAgent.id);

    expect(container.textContent).toContain("Loading Agent profile");
    expect(container.textContent).not.toContain(agentFixture.name);
    expect(container.textContent).not.toContain(nextEvent.title);
    expect(container.querySelector("a[href='/activity?agent_id=test-bot']")).toBeNull();

    await act(async () => {
      nextSessions.resolve(response(sessionsPage(nextAgent)));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.querySelector(".agent-profile h1")?.textContent).toBe(nextAgent.name);
    expect(container.querySelector(".agent-activity-section")?.textContent).toContain(nextEvent.title);
    expect(container.querySelector("a[href='/activity?agent_id=next-bot']")).not.toBeNull();
  });
});
