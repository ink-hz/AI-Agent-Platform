import type { ClusterSnapshot, FleetOverview } from "./types";


export async function fetchClusterStatus(
  signal?: AbortSignal,
): Promise<ClusterSnapshot> {
  const response = await fetch("/api/cluster/status", { signal });
  if (!response.ok) throw new Error(`cluster ${response.status}`);
  return response.json();
}


export async function fetchFleetOverview(
  signal?: AbortSignal,
): Promise<FleetOverview> {
  const response = await fetch("/api/fleet/overview", { signal });
  if (!response.ok) throw new Error(`fleet ${response.status}`);
  return response.json();
}
