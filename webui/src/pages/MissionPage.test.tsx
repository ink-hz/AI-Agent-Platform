/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { BrainApiError } from "../brainApi";
import type { Mission, MissionEvent } from "../brainTypes";
import { MissionPage, type MissionPageClient } from "./MissionPage";


const account: Account = {
  internal_user_id: "member", display_name: "洛奇", role: "member",
  observation_agent_ids: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};

const active: Mission = {
  mission_id: "4e2ac19d-00cc-43ca-a953-f678b8bf7029", mode: "brain",
  direct_agent_id: null, status: "planning", cancel_requested: false, row_version: 1,
  created_at: "2026-08-22T10:00:00Z", updated_at: "2026-08-22T10:00:00Z",
  terminal_at: null, prompt: "找视觉人才", content_available: true,
};

function abortWait(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
}


describe("MissionPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    window.history.replaceState({}, "", `/missions/${active.mission_id}`);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("refetches a snapshot before reconnecting after the last accepted SSE id", async () => {
    const calls: string[] = [];
    let streamAttempt = 0;
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockImplementation(async () => { calls.push("snapshot"); return active; }),
      cancelMission: vi.fn(),
      streamMissionEvents: vi.fn().mockImplementation(async (_id, options) => {
        streamAttempt += 1;
        calls.push(`stream:${options.after}`);
        if (streamAttempt === 1) {
          options.onEvent({
            event_id: "event-1", mission_id: active.mission_id, run_id: null, seq: 7,
            event_type: "brain.responding", payload: { text: "正在分析需求" },
            created_at: "2026-08-22T10:00:07Z",
          } satisfies MissionEvent);
          throw new TypeError("network disconnected");
        }
        await abortWait(options.signal);
      }),
      reconnectDelay: async () => undefined,
    };

    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(calls.slice(0, 4)).toEqual(["snapshot", "stream:0", "snapshot", "stream:7"]);
    expect(container.textContent).toContain("正在分析需求");
    expect(container.textContent).not.toContain("任务执行失败");
  });

  it("shows a friendly offline state and aborts the stream on unmount", async () => {
    let observedSignal: AbortSignal | undefined;
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockResolvedValue(active),
      cancelMission: vi.fn(),
      streamMissionEvents: vi.fn().mockImplementation(async (_id, options) => {
        observedSignal = options.signal;
        throw new TypeError("offline");
      }),
      reconnectDelay: (signal) => abortWait(signal),
    };
    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    expect(container.textContent).toContain("连接暂时中断");
    expect(container.textContent).toContain("任务仍会保留");

    await act(async () => root.unmount());
    expect(observedSignal?.aborted).toBe(true);
  });

  it("sends a CSRF-protected stop request and keeps the persisted URL visible", async () => {
    const cancelled = { ...active, cancel_requested: true, row_version: 2 };
    const cancelMission = vi.fn().mockResolvedValue(cancelled);
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockResolvedValue(active), cancelMission,
      streamMissionEvents: vi.fn().mockImplementation(async (_id, options) => abortWait(options.signal)),
      reconnectDelay: (signal) => abortWait(signal),
    };
    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve();
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".mission-cancel")?.click());

    expect(cancelMission).toHaveBeenCalledWith(active.mission_id, "csrf", expect.any(AbortSignal));
    expect(container.textContent).toContain("正在停止");
    expect(container.querySelector<HTMLAnchorElement>(".mission-permalink")?.getAttribute("href"))
      .toBe(`/missions/${active.mission_id}`);
  });

  it("replays persisted cards for a terminal Mission before closing the stream", async () => {
    const terminal = { ...active, status: "completed" as const, terminal_at: "2026-08-22T10:01:00Z" };
    const reconnectDelay = vi.fn();
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockResolvedValue(terminal),
      cancelMission: vi.fn(),
      streamMissionEvents: vi.fn().mockImplementation(async (_id, options) => {
        options.onEvent({
          event_id: "event-final", mission_id: active.mission_id, run_id: "run", seq: 8,
          event_type: "mission.completed", payload: { text: "# 已保存的最终结果" },
          created_at: "2026-08-22T10:01:00Z",
        } satisfies MissionEvent);
      }),
      reconnectDelay,
    };

    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(client.streamMissionEvents).toHaveBeenCalledWith(active.mission_id, expect.objectContaining({ after: 0 }));
    expect(container.textContent).toContain("已保存的最终结果");
    expect(reconnectDelay).not.toHaveBeenCalled();
  });

  it("passes the persisted direct Agent identity into terminal event attribution", async () => {
    const terminal = {
      ...active,
      mode: "direct_agent" as const,
      direct_agent_id: "hr-bot",
      status: "completed" as const,
      terminal_at: "2026-08-22T10:01:00Z",
    };
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockResolvedValue(terminal),
      cancelMission: vi.fn(),
      streamMissionEvents: vi.fn().mockImplementation(async (_id, options) => {
        options.onEvent({
          event_id: "direct-final", mission_id: active.mission_id, run_id: "run", seq: 8,
          event_type: "mission.completed", payload: { text: "# 专业 Agent 交付" },
          created_at: "2026-08-22T10:01:00Z",
        } satisfies MissionEvent);
      }),
      reconnectDelay: vi.fn(),
    };

    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(container.querySelector(".mission-event header span")?.textContent).toBe("专业 Agent · hr-bot");
  });

  it("aborts an in-flight stop request when the page unmounts", async () => {
    let cancelSignal: AbortSignal | undefined;
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockResolvedValue(active),
      cancelMission: vi.fn().mockImplementation(async (_id, _csrf, signal) => {
        cancelSignal = signal;
        await abortWait(signal!);
        return active;
      }),
      streamMissionEvents: vi.fn().mockImplementation(async (_id, options) => abortWait(options.signal)),
      reconnectDelay: (signal) => abortWait(signal),
    };
    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve();
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".mission-cancel")?.click());
    expect(cancelSignal?.aborted).toBe(false);

    await act(async () => root.unmount());
    expect(cancelSignal?.aborted).toBe(true);
  });

  it("does not treat an early terminal SSE EOF as a complete replay", async () => {
    const terminal = { ...active, status: "completed" as const, terminal_at: "2026-08-22T10:01:00Z" };
    let attempt = 0;
    const calls: string[] = [];
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockImplementation(async () => { calls.push("snapshot"); return terminal; }),
      cancelMission: vi.fn(),
      streamMissionEvents: vi.fn().mockImplementation(async (_id, options) => {
        attempt += 1;
        calls.push(`stream:${options.after}`);
        if (attempt === 2) options.onEvent({
          event_id: "terminal", mission_id: active.mission_id, run_id: "run", seq: 9,
          event_type: "mission.completed", payload: { text: "# 完整交付" },
          created_at: "2026-08-22T10:01:00Z",
        });
      }),
      reconnectDelay: async () => undefined,
    };

    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      for (let index = 0; index < 10; index += 1) await Promise.resolve();
    });

    expect(calls).toContain("stream:0");
    expect(vi.mocked(client.streamMissionEvents).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(container.textContent).toContain("完整交付");
  });

  it("clears the old Mission and accepts reused event sequences when missionId changes", async () => {
    const second = { ...active, mission_id: "11111111-1111-4111-8111-111111111111", prompt: "第二个任务" };
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockImplementation(async (id) => id === active.mission_id ? active : second),
      cancelMission: vi.fn(),
      streamMissionEvents: vi.fn().mockImplementation(async (id, options) => {
        options.onEvent({
          event_id: `${id}-event`, mission_id: id, run_id: null, seq: 1,
          event_type: "brain.responding", payload: { text: id === active.mission_id ? "旧事件" : "新事件" },
          created_at: "2026-08-22T10:00:01Z",
        });
        await abortWait(options.signal);
      }),
      reconnectDelay: (signal) => abortWait(signal),
    };
    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve();
    });
    expect(container.textContent).toContain("旧事件");

    await act(async () => {
      root.render(<MissionPage missionId={second.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    expect(container.textContent).toContain("第二个任务");
    expect(container.textContent).toContain("新事件");
    expect(container.textContent).not.toContain("旧事件");
    expect(container.textContent).not.toContain("找视觉人才");
  });

  it("explains when replay stopped because enterprise login expired", async () => {
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockRejectedValue(new BrainApiError(401)),
      cancelMission: vi.fn(), streamMissionEvents: vi.fn(),
      reconnectDelay: (signal) => abortWait(signal),
    };
    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve();
    });

    expect(container.textContent).toContain("企业登录已失效");
    expect(container.querySelector<HTMLAnchorElement>(
      `a[href="/login?return_path=%2Fmissions%2F${active.mission_id}"]`,
    )).not.toBeNull();
  });

  it("accepts a persisted professional failure as the terminal event for partial completion", async () => {
    const partial = { ...active, status: "partially_completed" as const, terminal_at: "2026-08-22T10:01:00Z" };
    const streamMissionEvents = vi.fn().mockImplementation(async (_id, options) => options.onEvent({
      event_id: "partial-failure", mission_id: active.mission_id, run_id: "run", seq: 8,
      event_type: "mission.failed", payload: { text: "专业 Agent 执行失败，已保留现有结果" },
      created_at: "2026-08-22T10:01:00Z",
    }));
    const client: MissionPageClient = {
      fetchMission: vi.fn().mockResolvedValue(partial), cancelMission: vi.fn(), streamMissionEvents,
      reconnectDelay: vi.fn(),
    };

    await act(async () => {
      root.render(<MissionPage missionId={active.mission_id} account={account} client={client} />);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(container.textContent).toContain("任务部分完成");
    expect(streamMissionEvents).toHaveBeenCalledTimes(1);
    expect(client.reconnectDelay).not.toHaveBeenCalled();
  });
});
