import type { ClusterSnapshot } from "./types";


export async function fetchClusterStatus(): Promise<ClusterSnapshot> {
  const response = await fetch("/api/cluster/status");
  if (!response.ok) throw new Error(`cluster ${response.status}`);
  return response.json();
}
