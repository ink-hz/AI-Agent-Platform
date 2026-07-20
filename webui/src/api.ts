import type { ClusterSnapshot } from "./types";


export async function fetchClusterStatus(
  signal?: AbortSignal,
): Promise<ClusterSnapshot> {
  const response = await fetch("/api/cluster/status", { signal });
  if (!response.ok) throw new Error(`cluster ${response.status}`);
  return response.json();
}
