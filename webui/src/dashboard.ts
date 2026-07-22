import type { ClusterSnapshot } from "./types";


export interface DashboardState {
  snapshot: ClusterSnapshot | null;
  degraded: boolean;
}


export const initialDashboardState: DashboardState = {
  snapshot: null,
  degraded: false,
};


export function applySuccess(
  _state: DashboardState,
  snapshot: ClusterSnapshot,
): DashboardState {
  return { snapshot, degraded: false };
}


export function applyFailure(state: DashboardState): DashboardState {
  return { ...state, degraded: true };
}


type Schedule = (callback: () => void, delay: number) => number;
type Cancel = (handle: number) => void;

interface TimedPollingCycle<T> {
  controller: AbortController;
  request: (signal: AbortSignal) => Promise<T>;
  isDisposed: () => boolean;
  onSuccess: (value: T) => void;
  onFailure: () => void;
  timeoutMs: number;
}


/**
 * Runs one deadline-bound request. The request must cooperate with AbortSignal;
 * otherwise the returned promise cannot settle until the underlying work does.
 */
export async function runTimedPollingCycle<T>({
  controller,
  request,
  isDisposed,
  onSuccess,
  onFailure,
  timeoutMs,
}: TimedPollingCycle<T>): Promise<void> {
  let failureApplied = false;
  const failOnce = () => {
    if (failureApplied || isDisposed()) return;
    failureApplied = true;
    onFailure();
  };
  const timeout = window.setTimeout(() => {
    controller.abort();
    failOnce();
  }, timeoutMs);

  try {
    const value = await request(controller.signal);
    if (isDisposed()) return;
    if (controller.signal.aborted) {
      failOnce();
      return;
    }
    onSuccess(value);
  } catch {
    failOnce();
  } finally {
    window.clearTimeout(timeout);
  }
}


export function startPolling(
  task: () => Promise<void>,
  intervalMs: number,
  schedule: Schedule = (callback, delay) => window.setTimeout(callback, delay),
  cancel: Cancel = (handle) => window.clearTimeout(handle),
): () => void {
  let stopped = false;
  let handle: number | null = null;

  const tick = async () => {
    try {
      await task();
    } catch {
      // The caller owns UI error state. Polling must continue after failures.
    } finally {
      if (!stopped) {
        handle = schedule(() => {
          void tick();
        }, intervalMs);
      }
    }
  };

  void tick();
  return () => {
    stopped = true;
    if (handle !== null) cancel(handle);
  };
}
