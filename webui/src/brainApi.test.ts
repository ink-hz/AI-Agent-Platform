/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { createMissionSubmission, streamMissionEvents } from "./brainApi";


afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});


describe("Agent Brain API", () => {
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
