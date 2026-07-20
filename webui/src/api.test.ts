import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchClusterStatus } from "./api";


describe("fetchClusterStatus", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes the caller abort signal to fetch", async () => {
    const controller = new AbortController();
    const response = {
      ok: true,
      json: vi.fn().mockResolvedValue({
        summary: { total: 0, healthy: 0, degraded: 0, offline: 0, checking: 0 },
        source: { healthy: true, checked_at: null, error: null },
        instances: [],
      }),
    };
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await fetchClusterStatus(controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/api/cluster/status", {
      signal: controller.signal,
    });
  });
});
