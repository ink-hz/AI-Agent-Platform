import { describe, expect, it } from "vitest";

import {
  FLEET_STATE_META,
  applyFleetFailure,
  applyFleetSuccess,
  formatChange,
  formatCount,
  formatRelativeActivity,
  initialFleetState,
  usageIsReadable,
} from "./fleet";
import type { FleetOverview } from "./types";


const overview: FleetOverview = {
  summary: {
    total_agents: 1,
    running_agents: 1,
    active_agents: 1,
    degraded_agents: 0,
    offline_agents: 0,
    checking_agents: 0,
    total_conversations: 14,
    conversations_last_7d: 4,
    conversations_previous_7d: 2,
    change_percent: 100,
  },
  trend: [],
  agents: [],
  runtime_source: { healthy: true, checked_at: null, stale: false, error: null },
  usage_source: { healthy: true, checked_at: null, stale: false, error: null },
};


describe("fleet dashboard state", () => {
  it("keeps the last success when a refresh fails", () => {
    const loaded = applyFleetSuccess(initialFleetState, overview);
    const failed = applyFleetFailure(loaded);

    expect(failed.overview).toBe(overview);
    expect(failed.degraded).toBe(true);
  });
});


describe("fleet presentation formatting", () => {
  it("formats product counts without inventing missing values", () => {
    expect(formatCount(8642)).toBe("8,642");
    expect(formatCount(null)).toBe("—");
  });

  it("formats relative activity for fast scanning", () => {
    const now = new Date("2026-07-21T01:02:00Z");
    expect(formatRelativeActivity("2026-07-21T01:00:00Z", now)).toBe("2分钟前");
    expect(formatRelativeActivity(null, now)).toBe("暂无活动");
  });

  it("formats period change without implying a comparison when none exists", () => {
    expect(formatChange(18)).toBe("较上期 +18%");
    expect(formatChange(-6.5)).toBe("较上期 -6.5%");
    expect(formatChange(null)).toBe("暂无对比");
  });

  it("only treats current or preserved real usage as readable", () => {
    expect(usageIsReadable({ healthy: true, checked_at: null, stale: false, error: null })).toBe(true);
    expect(usageIsReadable({ healthy: false, checked_at: "2026-07-21T02:00:00Z", stale: true, error: "usage_unavailable" })).toBe(true);
    expect(usageIsReadable({ healthy: false, checked_at: null, stale: false, error: "usage_unavailable" })).toBe(false);
  });

  it("uses the approved English state vocabulary", () => {
    expect(Object.values(FLEET_STATE_META).map((state) => state.label)).toEqual([
      "Active", "Online", "Degraded", "Offline", "Checking", "Unknown",
    ]);
  });
});
