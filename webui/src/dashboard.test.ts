import { describe, expect, it } from "vitest";

import { applyFailure, applySuccess, initialDashboardState } from "./dashboard";
import type { ClusterSnapshot } from "./types";


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
