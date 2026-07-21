import { UI_COPY } from "./copy";
import type { DataSourceStatus, FleetOverview, FleetState, LifecycleBasis } from "./types";


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


export function formatLifecycleDate(value: string | null): string {
  if (value === null) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not recorded";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}


export function formatDaysInProduction(value: string | null, now = new Date()): string {
  if (value === null) return "Not recorded";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Not recorded";
  const days = Math.floor(Math.max(0, now.getTime() - timestamp) / 86_400_000);
  if (days === 0) return "Today";
  return `${days} day${days === 1 ? "" : "s"}`;
}


export function formatExactLifecycleTime(value: string | null): string | undefined {
  if (value === null) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}


export function formatLastUpdated(value: string | null, now = new Date()): string {
  if (value === null) return "Not recorded";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Not recorded";
  const seconds = Math.max(0, Math.floor((now.getTime() - timestamp) / 1000));
  if (seconds < 60) return "Just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
  const years = Math.floor(days / 365);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}


const LIFECYCLE_BASIS_COPY: Record<LifecycleBasis, string> = {
  release_artifact: "Based on production release record",
  repository_history: "Based on deployed repository history",
  earliest_session: "Based on earliest captured Session",
  not_recorded: "No verified deployment record",
};


export function formatLifecycleBasis(basis: LifecycleBasis): string {
  return LIFECYCLE_BASIS_COPY[basis];
}


export function formatRuntimeDuration(seconds: number | null): string {
  if (seconds === null) return "Not available";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days} day${days === 1 ? "" : "s"}`);
  if (hours) parts.push(`${hours} hour${hours === 1 ? "" : "s"}`);
  if (!parts.length) {
    return minutes ? `${minutes} minute${minutes === 1 ? "" : "s"}` : "< 1 minute";
  }
  return parts.slice(0, 2).join(" ");
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
  active: { label: UI_COPY.states.active, tone: "active" },
  online: { label: UI_COPY.states.online, tone: "online" },
  degraded: { label: UI_COPY.states.degraded, tone: "degraded" },
  offline: { label: UI_COPY.states.offline, tone: "offline" },
  checking: { label: UI_COPY.states.checking, tone: "checking" },
  unknown: { label: "Unknown", tone: "checking" },
};
