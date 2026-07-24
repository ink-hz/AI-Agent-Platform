import { describe, expect, it } from "vitest";
import * as fleetFormatting from "./fleet";

import {
  FLEET_STATE_META,
  applyFleetFailure,
  applyFleetSuccess,
  formatChange,
  formatCount,
  formatLastUpdated,
  formatLifecycleDate,
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

  it("formats durable lifecycle dates without inventing missing evidence", () => {
    const now = new Date("2026-07-21T02:00:00Z");
    expect(formatLifecycleDate("2026-06-17T16:34:33+08:00")).toBe("2026年6月17日");
    expect(formatLifecycleDate(null)).toBe("未记录");
    expect(formatLastUpdated("2026-07-20T23:00:00Z", now)).toBe("3小时前");
    expect(formatLastUpdated(null, now)).toBe("未记录");
  });

  it("explains lifecycle evidence and formats diagnostic runtime in Chinese", () => {
    const formatting = fleetFormatting as unknown as {
      formatDaysInProduction: (value: string | null, now: Date) => string;
      formatLifecycleBasis: (basis: string) => string;
      formatRuntimeDuration: (seconds: number | null) => string;
    };
    expect(formatting.formatLifecycleBasis("earliest_session")).toBe("依据最早采集的 Session");
    expect(formatting.formatLifecycleBasis("release_artifact")).toBe("依据生产发布记录");
    expect(formatting.formatRuntimeDuration(90_061)).toBe("1天 1小时");
    expect(formatting.formatRuntimeDuration(null)).toBe("暂不可用");
  });

  it("formats elapsed full days in production from durable lifecycle data", () => {
    const formatting = fleetFormatting as unknown as {
      formatDaysInProduction: (value: string | null, now: Date) => string;
    };
    const now = new Date("2026-07-21T02:00:00Z");
    expect(formatting.formatDaysInProduction("2026-07-21T01:00:00Z", now)).toBe("今天上线");
    expect(formatting.formatDaysInProduction("2026-07-20T02:00:00Z", now)).toBe("已运行 1 天");
    expect(formatting.formatDaysInProduction("2026-06-17T02:00:00Z", now)).toBe("已运行 34 天");
    expect(formatting.formatDaysInProduction(null, now)).toBe("未记录");
    expect(formatting.formatDaysInProduction("invalid", now)).toBe("未记录");
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

  it("uses Chinese system state vocabulary", () => {
    expect(Object.values(FLEET_STATE_META).map((state) => state.label)).toEqual([
      "活跃", "在线", "异常", "离线", "检查中", "未知",
    ]);
  });
});
