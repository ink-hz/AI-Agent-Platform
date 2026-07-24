/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyFailure,
  applySuccess,
  initialDashboardState,
  startPolling,
} from "./dashboard";
import { fetchFleetOverview, fetchOperationsBrief } from "./api";
import { OverviewPage } from "./pages/OverviewPage";
import type { ClusterSnapshot, FleetOverview, OperationsBrief } from "./types";


vi.mock("./api", () => ({
  fetchFleetOverview: vi.fn(),
  fetchOperationsBrief: vi.fn(),
}));


const snapshot: ClusterSnapshot = {
  summary: { total: 1, healthy: 1, degraded: 0, offline: 0, checking: 0 },
  source: { healthy: true, checked_at: "2026-07-20T10:00:00Z", error: null },
  instances: [
    {
      id: "hr-bot",
      name: "hr-bot",
      pm2_name: "metabot-hr",
      port: 9101,
      status: "healthy",
      uptime_seconds: 42,
      latency_ms: 2,
      checked_at: "2026-07-20T10:00:00Z",
      error: null,
    },
  ],
};

const fleetOverview: FleetOverview = {
  summary: {
    total_agents: 1,
    running_agents: 1,
    active_agents: 1,
    degraded_agents: 0,
    offline_agents: 0,
    checking_agents: 0,
    total_conversations: 42,
    conversations_last_7d: 7,
    conversations_previous_7d: 5,
    change_percent: 40,
  },
  trend: [],
  agents: [],
  runtime_source: { healthy: true, checked_at: "2026-07-22T10:00:00Z", stale: false, error: null },
  usage_source: { healthy: true, checked_at: "2026-07-22T10:00:00Z", stale: false, error: null },
};

const operationsBrief: OperationsBrief = {
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
  usage: { conversations: 7, active_agents: 1, leaders: [] },
  changes: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((onResolve) => {
    resolve = onResolve;
  });
  return { promise, resolve };
}


describe("dashboard state", () => {
  it("stores a successful snapshot and clears degradation", () => {
    expect(applySuccess({ snapshot: null, degraded: true }, snapshot)).toEqual({
      snapshot,
      degraded: false,
    });
  });

  it("keeps the previous snapshot when refresh fails", () => {
    const failed = applyFailure({ snapshot, degraded: false });

    expect(failed.snapshot).toBe(snapshot);
    expect(failed.degraded).toBe(true);
  });

  it("starts without a fabricated snapshot", () => {
    expect(initialDashboardState).toEqual({ snapshot: null, degraded: false });
  });
});


describe("startPolling", () => {
  it("schedules the next run only after the current run settles", async () => {
    let release: (() => void) | undefined;
    let scheduled: (() => void) | undefined;
    let calls = 0;
    const task = () => {
      calls += 1;
      return new Promise<void>((resolve) => {
        release = resolve;
      });
    };
    const schedule = (callback: () => void) => {
      scheduled = callback;
      return 1;
    };

    const stop = startPolling(task, 10_000, schedule, () => undefined);
    await Promise.resolve();
    expect(calls).toBe(1);
    expect(scheduled).toBeUndefined();

    release?.();
    await Promise.resolve();
    await Promise.resolve();
    expect(scheduled).toBeTypeOf("function");

    stop();
  });
});


describe("Overview status messaging", () => {
  it("does not render a runtime availability banner", () => {
    const source = readFileSync("src/pages/OverviewPage.tsx", "utf8");

    expect(source).not.toContain("runtimeNeedsAttention");
    expect(source).not.toContain("UI_COPY.failures.runtime");
  });

  it("renders only Business Agents from the diagnostic Fleet payload", () => {
    const source = readFileSync("src/pages/OverviewPage.tsx", "utf8");

    expect(source).toContain("businessAgents");
    expect(source).toContain("businessAgents(overview.agents)");
    expect(source).not.toContain('agent.id !== "test-bot"');
    expect(source).not.toContain('agent.id !== "feishu-default"');
  });
});


describe("Overview Fleet and Daily Brief integration", () => {
  let container: HTMLDivElement;
  let root: Root | null;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(fetchFleetOverview).mockReset();
    vi.mocked(fetchOperationsBrief).mockReset();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    if (root !== null) await act(async () => root?.unmount());
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  async function renderOverview() {
    await act(async () => root?.render(createElement(OverviewPage)));
  }

  it("renders Fleet independently when the initial Operations request fails", async () => {
    vi.mocked(fetchFleetOverview).mockResolvedValue(fleetOverview);
    vi.mocked(fetchOperationsBrief).mockRejectedValue(new Error("operations unavailable"));

    await renderOverview();

    expect(container.querySelector(".summary-section")).not.toBeNull();
    expect(container.querySelector("h1")?.textContent).toBe("Agent 集群总览");
    expect(container.textContent).toContain("集群概况");
    expect(container.textContent).toContain("近 7 天使用趋势");
    expect(container.textContent).toContain("Agent 运行情况");
    expect(container.textContent).not.toMatch(/Fleet Snapshot|Active Agents|Read-only/);
    expect(container.textContent).toContain("42");
    expect(container.querySelector(".daily-brief")).toBeNull();
    expect(container.querySelector(".insight-grid")).not.toBeNull();
    expect(fetchFleetOverview).toHaveBeenCalledTimes(1);
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(1);
  });

  it("retains the exact last Brief as stale after a later Operations failure and keeps DOM order", async () => {
    vi.mocked(fetchFleetOverview).mockResolvedValue(fleetOverview);
    vi.mocked(fetchOperationsBrief)
      .mockResolvedValueOnce(operationsBrief)
      .mockRejectedValueOnce(new Error("operations unavailable"));

    await renderOverview();

    const summary = container.querySelector(".summary-section")!;
    const brief = container.querySelector(".daily-brief")!;
    const insights = container.querySelector(".insight-grid")!;
    expect(summary.compareDocumentPosition(brief) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(brief.compareDocumentPosition(insights) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(brief.textContent).toContain("AI FAE Agent is offline");

    await act(async () => vi.advanceTimersByTimeAsync(10_000));

    expect(container.querySelector(".summary-section")).not.toBeNull();
    expect(container.querySelector(".daily-brief")?.textContent).toContain("AI FAE Agent is offline");
    expect(container.querySelector(".daily-brief")?.textContent).toContain("运行摘要数据已过期");
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(2);
  });

  it("aborts both active request lifecycles on cleanup and ignores late resolutions", async () => {
    const fleet = deferred<FleetOverview>();
    const operations = deferred<OperationsBrief>();
    vi.mocked(fetchFleetOverview).mockReturnValue(fleet.promise);
    vi.mocked(fetchOperationsBrief).mockReturnValue(operations.promise);

    await renderOverview();

    const fleetSignal = vi.mocked(fetchFleetOverview).mock.calls[0][0]!;
    const operationsSignal = vi.mocked(fetchOperationsBrief).mock.calls[0][0]!;
    await act(async () => root?.unmount());
    root = null;

    expect(fleetSignal.aborted).toBe(true);
    expect(operationsSignal.aborted).toBe(true);
    await act(async () => {
      fleet.resolve(fleetOverview);
      operations.resolve(operationsBrief);
      await Promise.resolve();
    });
    await vi.advanceTimersByTimeAsync(60_000);

    expect(container.textContent).toBe("");
    expect(fetchFleetOverview).toHaveBeenCalledTimes(1);
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(1);
  });

  it("ignores an Operations response that resolves after its request was aborted", async () => {
    const operations = deferred<OperationsBrief>();
    vi.mocked(fetchFleetOverview).mockResolvedValue(fleetOverview);
    vi.mocked(fetchOperationsBrief).mockReturnValue(operations.promise);

    await renderOverview();
    const signal = vi.mocked(fetchOperationsBrief).mock.calls[0][0]!;
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(signal.aborted).toBe(true);

    await act(async () => {
      operations.resolve(operationsBrief);
      await Promise.resolve();
    });

    expect(container.querySelector(".daily-brief")).toBeNull();
  });

  it("marks the last Fleet result degraded when a next-cycle timeout later resolves", async () => {
    const lateFleet = deferred<FleetOverview>();
    const replacementFleet: FleetOverview = {
      ...fleetOverview,
      summary: { ...fleetOverview.summary, total_agents: 999, total_conversations: 999 },
    };
    vi.mocked(fetchFleetOverview)
      .mockResolvedValueOnce(fleetOverview)
      .mockReturnValueOnce(lateFleet.promise);
    vi.mocked(fetchOperationsBrief).mockResolvedValue(operationsBrief);

    await renderOverview();
    expect(container.querySelector(".summary-section")?.textContent).toContain("42");

    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    const signal = vi.mocked(fetchFleetOverview).mock.calls[1][0]!;
    await act(async () => vi.advanceTimersByTimeAsync(5_000));

    expect(signal.aborted).toBe(true);
    expect(container.querySelector(".error-banner")).not.toBeNull();
    expect(container.querySelector(".summary-section")?.textContent).toContain("42");
    expect(fetchFleetOverview).toHaveBeenCalledTimes(2);

    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchFleetOverview).toHaveBeenCalledTimes(2);
    await act(async () => {
      lateFleet.resolve(replacementFleet);
      await Promise.resolve();
    });

    expect(container.querySelector(".error-banner")).not.toBeNull();
    expect(container.querySelector(".summary-section")?.textContent).toContain("42");
    expect(container.textContent).not.toContain("999");
    expect(fetchFleetOverview).toHaveBeenCalledTimes(2);
  });

  it("marks the last Brief stale when a next-cycle timeout later resolves", async () => {
    const lateBrief = deferred<OperationsBrief>();
    const replacementBrief: OperationsBrief = {
      ...operationsBrief,
      attention: [{ ...operationsBrief.attention[0], event_id: "late", title: "Late Operations payload" }],
    };
    vi.mocked(fetchFleetOverview).mockResolvedValue(fleetOverview);
    vi.mocked(fetchOperationsBrief)
      .mockResolvedValueOnce(operationsBrief)
      .mockReturnValueOnce(lateBrief.promise);

    await renderOverview();
    expect(container.querySelector(".daily-brief")?.textContent).toContain("AI FAE Agent is offline");

    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    const signal = vi.mocked(fetchOperationsBrief).mock.calls[1][0]!;
    await act(async () => vi.advanceTimersByTimeAsync(5_000));

    expect(signal.aborted).toBe(true);
    expect(container.querySelector(".daily-brief")?.textContent).toContain("运行摘要数据已过期");
    expect(container.querySelector(".daily-brief")?.textContent).toContain("AI FAE Agent is offline");
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(2);

    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(2);
    await act(async () => {
      lateBrief.resolve(replacementBrief);
      await Promise.resolve();
    });

    expect(container.querySelector(".daily-brief")?.textContent).toContain("运行摘要数据已过期");
    expect(container.querySelector(".daily-brief")?.textContent).toContain("AI FAE Agent is offline");
    expect(container.textContent).not.toContain("Late Operations payload");
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(2);
  });

  it("does not overlap or duplicate Operations requests within a polling cycle", async () => {
    const first = deferred<OperationsBrief>();
    const second = deferred<OperationsBrief>();
    vi.mocked(fetchFleetOverview).mockResolvedValue(fleetOverview);
    vi.mocked(fetchOperationsBrief)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    await renderOverview();
    await act(async () => vi.advanceTimersByTimeAsync(4_999));
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve(operationsBrief);
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(2);

    await act(async () => vi.advanceTimersByTimeAsync(4_999));
    expect(fetchOperationsBrief).toHaveBeenCalledTimes(2);
    await act(async () => {
      second.resolve(operationsBrief);
      await Promise.resolve();
    });
  });
});
