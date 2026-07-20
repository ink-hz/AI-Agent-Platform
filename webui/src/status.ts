import type { InstanceState } from "./types";


export interface StatusMeta {
  label: string;
  tone: InstanceState;
}


const STATUS_META: Record<InstanceState, StatusMeta> = {
  healthy: { label: "健康", tone: "healthy" },
  degraded: { label: "异常", tone: "degraded" },
  offline: { label: "离线", tone: "offline" },
  checking: { label: "检测中", tone: "checking" },
};


const ERROR_LABELS: Record<string, string> = {
  timeout: "探测超时",
  connection_failed: "连接失败",
  invalid_json: "响应格式异常",
  status_not_ok: "服务状态异常",
};


export function statusMeta(status: InstanceState): StatusMeta {
  return STATUS_META[status];
}


export function formatUptime(seconds: number | null): string {
  if (seconds === null) return "—";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days}天`);
  if (days || hours) parts.push(`${hours}小时`);
  parts.push(`${minutes}分钟`);
  return parts.join(" ");
}


export function formatCheckedAt(checkedAt: string | null): string {
  if (checkedAt === null) return "—";
  const date = new Date(checkedAt);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}


export function isStale(checkedAt: string | null, now = new Date()): boolean {
  if (checkedAt === null) return true;
  const timestamp = new Date(checkedAt).getTime();
  return Number.isNaN(timestamp) || now.getTime() - timestamp > 30_000;
}


export function formatLatency(latencyMs: number | null): string {
  return latencyMs === null ? "—" : `${latencyMs} ms`;
}


export function errorLabel(error: string | null): string | null {
  if (error === null) return null;
  if (error.startsWith("http_")) return `HTTP ${error.slice(5)}`;
  return ERROR_LABELS[error] ?? "探测异常";
}
