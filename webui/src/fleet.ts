import type { DataSourceStatus, FleetOverview, FleetState } from "./types";


export interface FleetDashboardState {
  overview: FleetOverview | null;
  degraded: boolean;
}


export const initialFleetState: FleetDashboardState = {
  overview: null,
  degraded: false,
};


export function applyFleetSuccess(
  _state: FleetDashboardState,
  overview: FleetOverview,
): FleetDashboardState {
  return { overview, degraded: false };
}


export function applyFleetFailure(state: FleetDashboardState): FleetDashboardState {
  return { ...state, degraded: true };
}


export function formatCount(value: number | null): string {
  return value === null ? "—" : new Intl.NumberFormat("en-US").format(value);
}


export function formatRelativeActivity(
  value: string | null,
  now = new Date(),
): string {
  if (value === null) return "暂无活动";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "暂无活动";
  const seconds = Math.max(0, Math.floor((now.getTime() - timestamp) / 1000));
  if (seconds < 60) return "刚刚";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  return `${Math.floor(hours / 24)}天前`;
}


export function formatChange(value: number | null): string {
  if (value === null) return "暂无对比";
  const sign = value > 0 ? "+" : "";
  return `较上期 ${sign}${value}%`;
}


export function usageIsReadable(source: DataSourceStatus): boolean {
  return source.healthy || source.stale;
}


export const FLEET_STATE_META: Record<
  FleetState,
  { label: string; tone: string }
> = {
  active: { label: "活跃", tone: "active" },
  online: { label: "在线", tone: "online" },
  degraded: { label: "异常", tone: "degraded" },
  offline: { label: "离线", tone: "offline" },
  checking: { label: "检测中", tone: "checking" },
};
