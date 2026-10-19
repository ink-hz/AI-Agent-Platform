import { afterEach, expect, it, vi } from "vitest";

import { sdkApi } from "./sdkApi";

afterEach(() => vi.unstubAllGlobals());

it("writes a requirement through the same-origin API with CSRF and no actor field", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 12 }) });
  vi.stubGlobal("fetch", fetchMock);

  await sdkApi.createRequirement({
    request_id: "11111111-1111-4111-8111-111111111111",
    title: "USB 稳定性", scenario: "客户现场", device_platform: "Gemini 2 / Linux", acceptance: "连续运行",
  }, "csrf-value");

  expect(fetchMock).toHaveBeenCalledOnce();
  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe("/api/v1/sdk/requirements");
  expect(init.credentials).toBe("same-origin");
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-value");
  expect(JSON.parse(init.body as string)).not.toHaveProperty("actor_id");
});
