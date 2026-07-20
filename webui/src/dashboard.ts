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
