import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchClusterStatus,
  fetchFleetOverview,
  fetchOperationalEvents,
  fetchOperationsBrief,
} from "./api";


describe("fetchClusterStatus", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes the caller abort signal to fetch", async () => {
    const controller = new AbortController();
    const response = {
      ok: true,
      json: vi.fn().mockResolvedValue({
        summary: { total: 0, healthy: 0, degraded: 0, offline: 0, checking: 0 },
        source: { healthy: true, checked_at: null, error: null },
        instances: [],
      }),
    };
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await fetchClusterStatus(controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/api/cluster/status", {
      signal: controller.signal,
    });
  });
});


describe("fetchFleetOverview", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests the read-only fleet product endpoint", async () => {
    const controller = new AbortController();
    const overview = {
      summary: {
        total_agents: 0,
        running_agents: 0,
        active_agents: 0,
        degraded_agents: 0,
        offline_agents: 0,
        checking_agents: 0,
        total_conversations: 0,
        conversations_last_7d: 0,
        conversations_previous_7d: 0,
        change_percent: null,
      },
      trend: [],
      agents: [],
      runtime_source: { healthy: true, checked_at: null, stale: false, error: null },
      usage_source: { healthy: true, checked_at: null, stale: false, error: null },
    };
    const response = { ok: true, json: vi.fn().mockResolvedValue(overview) };
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchFleetOverview(controller.signal)).resolves.toEqual(overview);
    expect(fetchMock).toHaveBeenCalledWith("/api/fleet/overview", {
      signal: controller.signal,
    });
  });
});


describe("fetchOperationsBrief", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests the Operations Brief endpoint and forwards the abort signal", async () => {
    const controller = new AbortController();
    const brief = {
      period_start: "2026-07-21T10:00:00Z",
      period_end: "2026-07-22T10:00:00Z",
      freshness: { status: "current", evaluated_at: "2026-07-22T10:00:00Z", failed_groups: [] },
      can_claim_healthy: true,
      attention: [],
      usage: { conversations: 0, active_agents: 0, leaders: [] },
      changes: [],
    };
    const response = { ok: true, json: vi.fn().mockResolvedValue(brief) };
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchOperationsBrief(controller.signal)).resolves.toEqual(brief);
    expect(fetchMock).toHaveBeenCalledWith("/api/operations/brief", {
      signal: controller.signal,
    });
  });
});


describe("fetchOperationalEvents", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("serializes non-empty filters and forwards the abort signal", async () => {
    const controller = new AbortController();
    const page = { items: [], total: 0, limit: 50, offset: 0 };
    const response = { ok: true, json: vi.fn().mockResolvedValue(page) };
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchOperationalEvents({
      agent_id: "ai-fae-agent",
      event_type: "runtime_offline",
      severity: "critical",
      date_from: "2026-07-21T00:00:00+08:00",
      date_to: "2026-07-22T23:59:59+08:00",
      limit: 50,
      offset: 0,
    }, controller.signal)).resolves.toEqual(page);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/operations/events?agent_id=ai-fae-agent&event_type=runtime_offline&severity=critical&date_from=2026-07-21T00%3A00%3A00%2B08%3A00&date_to=2026-07-22T23%3A59%3A59%2B08%3A00&limit=50&offset=0",
      { signal: controller.signal },
    );
  });
});
