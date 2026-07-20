import { describe, expect, it } from "vitest";

import { formatUptime, isStale, statusMeta } from "./status";


describe("statusMeta", () => {
  it("maps every cluster state to an explicit Chinese label", () => {
    expect(statusMeta("healthy").label).toBe("健康");
    expect(statusMeta("degraded").label).toBe("异常");
    expect(statusMeta("offline").label).toBe("离线");
    expect(statusMeta("checking").label).toBe("检测中");
  });
});


describe("formatUptime", () => {
  it("shows an em dash when uptime is unavailable", () => {
    expect(formatUptime(null)).toBe("—");
  });

  it("formats days, hours, and minutes without hiding days", () => {
    expect(formatUptime(90_061)).toBe("1天 1小时 1分钟");
  });

  it("shows minutes for a new process", () => {
    expect(formatUptime(42)).toBe("0分钟");
  });
});


describe("isStale", () => {
  it("marks missing timestamps stale", () => {
    expect(isStale(null, new Date("2026-07-20T10:00:00Z"))).toBe(true);
  });

  it("marks a check older than 30 seconds stale", () => {
    expect(
      isStale(
        "2026-07-20T10:00:00Z",
        new Date("2026-07-20T10:00:31Z"),
      ),
    ).toBe(true);
  });

  it("keeps a check exactly 30 seconds old fresh", () => {
    expect(
      isStale(
        "2026-07-20T10:00:00Z",
        new Date("2026-07-20T10:00:30Z"),
      ),
    ).toBe(false);
  });
});
