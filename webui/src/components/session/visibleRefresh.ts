// One request at a time; hidden pages release timers and in-flight work.
export function startVisibleRefresh(request: (signal: AbortSignal) => Promise<void>): () => void {
  let disposed = false;
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const clear = () => {
    clearTimeout(timer);
    controller?.abort();
  };
  const refresh = () => {
    clear();
    if (disposed || document.visibilityState === "hidden") return;
    const current = new AbortController();
    controller = current;
    void Promise.resolve().then(() => {
      if (!current.signal.aborted) return request(current.signal);
    }).finally(() => {
      if (!disposed && !current.signal.aborted && document.visibilityState !== "hidden") {
        timer = setTimeout(refresh, 10_000);
      }
    });
  };
  const visibilityChanged = () => {
    if (document.visibilityState === "hidden") clear();
    else refresh();
  };
  document.addEventListener("visibilitychange", visibilityChanged);
  refresh();
  return () => {
    disposed = true;
    clear();
    document.removeEventListener("visibilitychange", visibilityChanged);
  };
}
