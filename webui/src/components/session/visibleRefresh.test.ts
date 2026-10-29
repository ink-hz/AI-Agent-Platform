/** @vitest-environment jsdom */
import { afterEach, expect, it, vi } from "vitest";
import { startVisibleRefresh } from "./visibleRefresh";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it("does not overlap slow requests and aborts when hidden or disposed", async () => {
  vi.useFakeTimers();
  let hidden = false;
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
  const signals: AbortSignal[] = [];
  const request = vi.fn((signal: AbortSignal) => {
    signals.push(signal);
    return new Promise<void>(() => undefined);
  });
  const dispose = startVisibleRefresh(request);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(request).toHaveBeenCalledTimes(1);
  hidden = true;
  document.dispatchEvent(new Event("visibilitychange"));
  expect(signals[0].aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(request).toHaveBeenCalledTimes(1);
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(0);
  expect(request).toHaveBeenCalledTimes(2);
  dispose();
  expect(signals[1].aborted).toBe(true);
  document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(30_000);
  expect(request).toHaveBeenCalledTimes(2);
});
