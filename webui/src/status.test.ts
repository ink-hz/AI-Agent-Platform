import { describe, expect, it } from "vitest";

import { statusBadge } from "./status";
import type { Agent } from "./types";

const agent = (overrides: Partial<Agent> = {}): Agent => ({
  id: "fae",
  name: "AI FAE Agent",
  domain: "技术支持",
  description: "",
  icon: "🛠️",
  owner: "",
  env: "prod",
  status: "active",
  entry_url: "http://fae/app/",
  version: "",
  tags: [],
  ...overrides,
});

describe("statusBadge", () => {
  it("shows 检测中 when health unknown", () => {
    expect(statusBadge(agent(), undefined).tone).toBe("unknown");
  });

  it("shows 在线 when online", () => {
    expect(statusBadge(agent(), {
      id: "fae",
      online: true,
      checked_at: null,
      latency_ms: 1,
      version: null,
      metrics: [],
    }).label).toBe("在线");
  });

  it("shows 离线 when offline", () => {
    expect(statusBadge(agent(), {
      id: "fae",
      online: false,
      checked_at: null,
      latency_ms: null,
      version: null,
      metrics: [],
    }).label).toBe("离线");
  });

  it("registry maintenance overrides health", () => {
    expect(statusBadge(agent({ status: "maintenance" }), {
      id: "fae",
      online: true,
      checked_at: null,
      latency_ms: 1,
      version: null,
      metrics: [],
    }).tone).toBe("maintenance");
  });
});
