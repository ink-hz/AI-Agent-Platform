/** @vitest-environment jsdom */
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { sessionStorage.clear(); vi.resetModules(); vi.restoreAllMocks(); });

it("recovers an uncertain request id from session storage without storing its payload", async () => {
  const randomUUID = vi.spyOn(crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000001");
  const firstModule = await import("./hrMutationRequest");
  const first = firstModule.retainMutationRequest("candidate-feedback", { correction: "敏感纠正" });
  vi.resetModules();
  const recovered = (await import("./hrMutationRequest")).retainMutationRequest("candidate-feedback", { correction: "敏感纠正" });

  expect(recovered.requestId).toBe(first.requestId);
  expect(randomUUID).toHaveBeenCalledTimes(1);
  expect(Object.keys(sessionStorage).join(" ")).not.toContain("敏感纠正");
});

it("allocates independent ids for interleaved payloads and clears only the completed one", async () => {
  vi.spyOn(crypto, "randomUUID")
    .mockReturnValueOnce("00000000-0000-4000-8000-000000000001")
    .mockReturnValueOnce("00000000-0000-4000-8000-000000000002")
    .mockReturnValueOnce("00000000-0000-4000-8000-000000000003");
  const requests = await import("./hrMutationRequest");
  const first = requests.retainMutationRequest("task", { kind: "match" });
  const second = requests.retainMutationRequest("task", { kind: "interview" });
  requests.completeMutationRequest(first.key);

  expect(requests.retainMutationRequest("task", { kind: "match" }).requestId).not.toBe(first.requestId);
  expect(requests.retainMutationRequest("task", { kind: "interview" }).requestId).toBe(second.requestId);
});
