import { afterEach, expect, it, vi } from "vitest";

import { nativeSdkApi } from "./nativeSdkApi";

afterEach(() => vi.unstubAllGlobals());

it("reads Q4 requirements through the platform origin with the platform session", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], page: 1, total: 0 }) });
  vi.stubGlobal("fetch", fetcher);

  await nativeSdkApi.requirements({ view: "q4", page: 2, query: "IMU 同步", status: "reviewing" });

  expect(fetcher).toHaveBeenCalledOnce();
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toContain("/api/v1/sdk/native/requirements?");
  expect(new URL(url, "https://agent.orbbec.com.cn").searchParams.get("query")).toBe("IMU 同步");
  expect(new URL(url, "https://agent.orbbec.com.cn").searchParams.get("view")).toBe("q4");
  expect(options.credentials).toBe("same-origin");
  expect(options.headers).toEqual({ Accept: "application/json" });
  expect(JSON.stringify(options)).not.toContain("Token");
});

it("reports upstream errors without exposing ZenTao credentials", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: "sdk_zentao_permission_denied" }) }));
  await expect(nativeSdkApi.requirement(69)).rejects.toMatchObject({ status: 403, detail: "sdk_zentao_permission_denied" });
});

it("creates a draft requirement with the platform CSRF token and an idempotency key", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({ status: "created", story_id: 70 }) });
  vi.stubGlobal("fetch", fetcher);
  const input = { request_id: "11111111-1111-4111-8111-111111111111", title: "支持新设备", scenario: "客户场景",
    device_platform: "Gemini 330 / Linux", acceptance: "多机连续运行", priority: 2,
    business_proposer: "客户 A", assigned_to: "sdkdev", reviewer: "reviewer" };
  await nativeSdkApi.createRequirement(input, "csrf-value");
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toContain("/api/v1/sdk/native/requirements");
  expect(options.method).toBe("POST");
  expect(options.headers["X-CSRF-Token"]).toBe("csrf-value");
  expect(JSON.parse(options.body)).toEqual(input);
});

it("sends review against the version shown in the UI", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 69, status: "active" }) });
  vi.stubGlobal("fetch", fetcher);
  await nativeSdkApi.review(69, { expected_version: 2, decision: "pass", comment: "已验证" }, "csrf-value");
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toContain("/requirements/69/review");
  expect(JSON.parse(options.body)).toEqual({ expected_version: 2, decision: "pass", comment: "已验证" });
  expect(options.headers["X-CSRF-Token"]).toBe("csrf-value");
});
