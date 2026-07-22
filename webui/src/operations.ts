import type { OperationalEvent, OperationsBrief } from "./types";


export interface OperationsBriefState {
  brief: OperationsBrief | null;
  stale: boolean;
}

export const initialOperationsState: OperationsBriefState = {
  brief: null,
  stale: false,
};

export function applyOperationsSuccess(
  _state: OperationsBriefState,
  brief: OperationsBrief,
): OperationsBriefState {
  return { brief, stale: false };
}

export function applyOperationsFailure(state: OperationsBriefState): OperationsBriefState {
  if (state.brief === null) return state;
  return { brief: state.brief, stale: true };
}

function evaluationTime(value: string | null): string | null {
  if (value === null) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Shanghai",
  }).format(date);
}

export function briefStatusLabel(
  freshness: OperationsBrief["freshness"],
  locallyStale = false,
): string {
  const time = evaluationTime(freshness.evaluated_at);
  if (locallyStale || freshness.status === "stale") {
    return time ? `Brief data is stale · Last evaluated ${time}` : "Brief data is stale";
  }
  if (freshness.status === "partial") {
    return time ? `Brief partially evaluated · ${time}` : "Brief partially evaluated";
  }
  if (freshness.status === "unavailable") return "Brief unavailable";
  return time ? `Evaluated ${time}` : "Evaluation time unavailable";
}

export function eventTimeLabel(event: OperationalEvent): string {
  const date = new Date(event.occurred_at);
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Shanghai",
  }).format(date);
}

export function eventTargetPath(event: OperationalEvent): string | null {
  const path = event.target_path;
  if (path === null || !path.startsWith("/") || path.startsWith("//") || path.includes("#")) return null;
  if (path === "/" || path === "/agents" || path === "/sessions" || path === "/flywheel") return path;
  if (/^\/(?:agents|sessions)\/[^/?#]+(?:\?[^#]*)?$/.test(path)) return path;
  if (/^\/sessions(?:\?[^#]*)?$/.test(path)) return path;
  return null;
}
