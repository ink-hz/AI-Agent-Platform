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
  return new Intl.DateTimeFormat("zh-CN", {
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
    return time ? `运行摘要数据已过期 · 最近计算于 ${time}` : "运行摘要数据已过期";
  }
  if (freshness.status === "partial") {
    return time ? `部分数据已计算 · ${time}` : "部分数据已计算";
  }
  if (freshness.status === "unavailable") return "运行摘要暂不可用";
  return time ? `计算于 ${time}` : "未记录计算时间";
}

export function eventTimeLabel(event: OperationalEvent): string {
  const date = new Date(event.occurred_at);
  if (Number.isNaN(date.getTime())) return "时间暂不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
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
