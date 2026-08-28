/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createMissionSubmission,
  fetchAgentCatalog,
  launchAgent,
  streamMissionEvents,
} from "./brainApi";


afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});


describe("Agent Brain API", () => {
  it("issues an authenticated one-time launch without exposing identity fields", async () => {
    const launchCode = "l".repeat(43);
    const launch = {
      launch_url: `https://fae.orbbec.com.cn/app/#platform_launch=${launchCode}`,
      expires_at: "2026-08-28T12:01:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(launch), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(launchAgent("ai-fae-agent", "csrf-memory-only")).resolves.toEqual(launch);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/agents/ai-fae-agent/launch",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-memory-only" }),
      }),
    );
  });

  it("accepts an Agent that has both a workspace and Brain delegation", async () => {
    const voc = {
      agent_id: "voc", display_name: "VOC Agent", persona_subtitle: null,
      domain_group: "客户洞察", mission: "分析客户声音并生成受控业务动作。",
      capabilities: ["VOC 分析"], exclusions: ["不绕过用户确认执行写操作"],
      example_tasks: ["分析近期客户反馈"], required_inputs: ["分析目标"],
      accepted_input_types: ["text"], output_types: ["text"],
      supports_attachments_in: false, supports_attachments_out: false,
      supports_evidence: true, supports_streaming: true,
      supports_cancellation: true, supports_idempotency: true,
      supports_persistent_session: true, supports_followup_message: true,
      supports_progress_events: true, supports_thinking_summary: true,
      supports_cancel: true, supports_attachments: false,
      typical_latency_seconds: 30, max_duration_seconds: 300,
      data_classification: "internal",
      interaction_modes: ["external_workspace", "brain_delegation"],
      workspace_url: "/agents/voc/workspace",
      adapter_id: "voc-extension", adapter_kind: "reference",
      adapter_config_version: 1, execution_pool: "platform_cloud",
      pool_concurrency: 4, output_contract: "normalized_task_result_v1",
      capability_version: 2, authorization_policy: "agent_grant",
      dispatchable: true,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ agents: [voc] }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    await expect(fetchAgentCatalog()).resolves.toEqual([voc]);
  });

  it("rejects a UTF-8 payload over 32 KiB before any network write", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    expect(() => createMissionSubmission("人".repeat(11_000), "csrf")).toThrow("32 KiB");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("retains one UUID idempotency key across a retried write", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("8c13c965-1b60-472e-b275-199987d1d109");
    const mission = {
      mission_id: "4e2ac19d-00cc-43ca-a953-f678b8bf7029",
      mode: "brain", direct_agent_id: null, status: "planning",
      cancel_requested: false, row_version: 1,
      created_at: "2026-08-22T10:00:00Z", updated_at: "2026-08-22T10:00:00Z",
      terminal_at: null, prompt: "找视觉人才", content_available: true,
    };
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(new Response(JSON.stringify(mission), {
        status: 201, headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const submission = createMissionSubmission("找视觉人才", "csrf-memory-only");

    await expect(submission.send()).rejects.toThrow();
    await expect(submission.send()).resolves.toEqual(mission);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toBe("/api/v1/brain/missions");
      expect(init).toMatchObject({
        method: "POST", credentials: "include", body: JSON.stringify({ text: "找视觉人才" }),
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": "csrf-memory-only",
          "Idempotency-Key": "8c13c965-1b60-472e-b275-199987d1d109",
        },
      });
    }
  });

  it("streams split SSE frames after the accepted sequence with credentials", async () => {
    const first = JSON.stringify({
      event_id: "one", mission_id: "mission", run_id: null, seq: 5,
      event_type: "agent.progress", payload: { text: "检索中" }, created_at: "2026-08-22T10:00:05Z",
    });
    const second = JSON.stringify({
      event_id: "two", mission_id: "mission", run_id: "run", seq: 6,
      event_type: "mission.completed", payload: { text: "# 完成" }, created_at: "2026-08-22T10:00:06Z",
    });
    const encoded = new TextEncoder().encode(`: heartbeat\n\nid: 5\nevent: mission\ndata: ${first}\n\nid: 6\nevent: mission\ndata: ${second}\n\n`);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded.slice(0, 37));
        controller.enqueue(encoded.slice(37));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, {
      status: 200, headers: { "Content-Type": "text/event-stream" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const events: unknown[] = [];

    await streamMissionEvents("mission", {
      after: 4,
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/brain/missions/mission/events?after=4",
      expect.objectContaining({ credentials: "include", signal: expect.any(AbortSignal) }),
    );
    expect(events).toEqual([
      expect.objectContaining({ seq: 5, event_type: "agent.progress" }),
      expect.objectContaining({ seq: 6, event_type: "mission.completed" }),
    ]);
  });
});
