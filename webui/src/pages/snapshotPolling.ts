export function scheduleSnapshotPolling(callback: () => void, intervalMs: number): () => void {
  const timer = globalThis.setInterval(callback, intervalMs);
  return () => globalThis.clearInterval(timer);
}
