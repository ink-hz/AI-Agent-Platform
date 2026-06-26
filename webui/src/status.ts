import type { Agent, Health } from "./types";

export type Badge = {
  label: string;
  tone: "online" | "offline" | "maintenance" | "unknown";
};

export function statusBadge(agent: Agent, health: Health | undefined): Badge {
  if (agent.status === "maintenance") {
    return { label: "维护中", tone: "maintenance" };
  }
  if (agent.status === "offline") {
    return { label: "已下线", tone: "offline" };
  }
  if (!health || health.online === null) {
    return { label: "检测中", tone: "unknown" };
  }
  return health.online
    ? { label: "在线", tone: "online" }
    : { label: "离线", tone: "offline" };
}
