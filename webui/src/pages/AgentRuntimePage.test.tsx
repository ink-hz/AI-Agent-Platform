/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentRuntimeView, AgentSummary } from "../types";
import { AgentRuntimePage } from "./AgentRuntimePage";


const agent: AgentSummary = {
  id: "marketing-inbound-bot", name: "Marketing Inbound", domain: "Marketing",
  description: "Inbound work", glyph: "IN", accent: "inbound", visibility: "business",
  source_kind: "metabot", deployment: "Local", session_count: 10, total_turns: 20,
  last_activity_at: null, last_synced_at: null, freshness: "live",
};

const runtime: AgentRuntimeView = {
  agent_id: agent.id,
  readiness: {
    status: "Ready", reason: "Runtime and primary channel are available",
    observed_at: "2026-07-24T08:00:00Z", freshness: "live",
  },
  runtime: {
    engine: "claude", model: "claude-opus-4-8", model_source: "runtime",
    backend: "pty", channel: "Feishu", channel_status: "connected",
    active_turns: 0, process_uptime_seconds: 3660,
  },
  lifecycle: {
    live_since: "2026-07-16T08:00:00Z", last_updated_at: "2026-07-23T08:00:00Z",
    production_runtime_seconds: 8 * 24 * 60 * 60,
  },
  evidence: [{
    kind: "runtime", source: "runtime_observation", status: "current",
    observed_at: "2026-07-24T08:00:00Z", summary: "Model, backend, and channel observation",
  }],
};

function response<T>(body: T): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}

describe("AgentRuntimePage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it("shows evidence, process uptime, and lifecycle separately", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith("/runtime")) return Promise.resolve(response(runtime));
      if (path === `/api/agents/${agent.id}`) return Promise.resolve(response(agent));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    }));

    await act(async () => root.render(<AgentRuntimePage agentId={agent.id} />));

    expect(container.querySelector("h1")?.textContent).toContain("Marketing Inbound 运行详情");
    expect(container.textContent).toContain("当前运行状态");
    expect(container.textContent).toContain("运行环境");
    expect(container.textContent).toContain("当前进程");
    expect(container.textContent).toContain("1小时 1分钟");
    expect(container.textContent).toContain("已运行 8 天");
    expect(container.textContent).toContain("运行时观测");
    expect(container.textContent).toContain("进程重启后重新计时");
    expect(container.textContent).toContain("运行周期");
    expect(container.textContent).toContain("观测依据");
    expect(container.textContent).toContain("实时运行观测");
    expect(container.textContent).not.toContain("Model, backend, and channel observation");
    expect(container.textContent).not.toContain("Runtime and primary channel are available");
    expect(container.textContent).toContain("runtime_observation");
    expect(container.querySelector("a[href='/agents/marketing-inbound-bot']")).not.toBeNull();
    expect(document.title).toBe("运行详情 · Marketing Inbound · Orbbec Agent Platform");
  });

  it("renders unknown evidence as information rather than a page error", async () => {
    const unknown = {
      ...runtime,
      readiness: { status: "Unknown", reason: "Current channel readiness has not been observed", observed_at: null, freshness: "unavailable" },
      runtime: { ...runtime.runtime, model: "Model not observed", model_source: "unavailable", channel_status: "unknown", process_uptime_seconds: null },
      evidence: [],
    } satisfies AgentRuntimeView;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => Promise.resolve(
      response(String(input).endsWith("/runtime") ? unknown : agent),
    )));

    await act(async () => root.render(<AgentRuntimePage agentId={agent.id} />));

    expect(container.textContent).toContain("未知");
    expect(container.textContent).toContain("尚未观测到 Model");
    expect(container.textContent).not.toContain("Current channel readiness has not been observed");
    expect(container.querySelector("[role=alert]")).toBeNull();
  });
});
