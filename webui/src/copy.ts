import type {
  ReadinessStatus,
  Freshness,
  RuntimeChannelStatus,
  RuntimeFreshness,
} from "./types";


export const UI_COPY = {
  navigation: ["总览", "Agent", "Session", "运行记录"],
  navigationLabel: "主导航",
  hero: {
    title: "Agent 集群总览",
    description: "查看已接入 Agent 的运行状态、真实使用情况和最近运行记录。",
    running: (count: number) => `${count} 个 Agent 正常运行`,
    attention: (count: number) => `${count} 个 Agent 需要关注`,
    loading: "正在读取 Agent 状态",
  },
  summary: {
    title: "集群概况",
    metrics: ["Agent 数量", "正常运行", "累计对话", "近 7 天对话"],
    updated: "更新时间",
    agentsHint: "已接入 Platform",
    activeHint: (count: number) => `${count} 个 Agent 最近有真实活动`,
    totalHint: "数据来自 Flywheel",
  },
  insights: {
    trend: "近 7 天使用趋势",
    ranking: "活跃 Agent",
    rankingHint: "按真实对话数量排序",
    conversations: (count: string) => `${count} 次对话`,
    emptyTrend: "近 7 天暂无真实对话",
    emptyRanking: "近 7 天暂无活跃 Agent",
  },
  agent: {
    sectionTitle: "Agent 运行情况",
    fields: ["累计对话", "近 7 天", "已上线", "最近更新", "最近工作"],
    refresh: (count: number) => `${count} 个 Agent · 每 10 秒自动刷新`,
    emptyRecent: "暂无真实对话记录",
  },
  runtime: {
    title: "运行状态",
    detail: "运行详情",
    model: "Model",
    backend: "Backend",
    trace: "Trace",
  },
  states: {
    active: "活跃",
    online: "在线",
    degraded: "异常",
    offline: "离线",
    checking: "检查中",
    unknown: "未知",
  },
  failures: {
    platform: "Platform 暂时无法获取最新状态，当前显示上一次成功数据并继续重试。",
    usage: "Flywheel 暂时不可用，当前显示上一次成功数据。",
    usageTitle: "对话数据暂不可用",
    usageDescription: "Agent 状态仍在更新，Platform 会继续读取 Flywheel。",
  },
  loading: {
    title: "正在加载 Agent 集群总览",
    failedTitle: "暂时无法读取 Agent 数据",
    description: "正在汇总 Agent 状态和真实对话数据。",
    retry: "Platform 会继续自动重试。",
  },
} as const;


const READINESS_LABELS: Record<ReadinessStatus, string> = {
  Ready: "正常",
  Busy: "忙碌",
  Limited: "受限",
  Offline: "离线",
  Unknown: "未知",
};

const READINESS_REASONS: Record<ReadinessStatus, string> = {
  Ready: "运行环境和主要 Channel 均正常",
  Busy: "Agent 正在处理任务",
  Limited: "部分运行能力或主要 Channel 受限",
  Offline: "Agent 当前离线",
  Unknown: "当前观测信息不足",
};

const FRESHNESS_LABELS: Record<RuntimeFreshness, string> = {
  live: "实时",
  stale: "数据已过期",
  unavailable: "暂不可用",
};

const CHANNEL_LABELS: Record<RuntimeChannelStatus, string> = {
  connected: "已连接",
  connecting: "连接中",
  reconnecting: "正在重连",
  failed: "连接失败",
  unknown: "未知",
};

const SOURCE_FRESHNESS_LABELS: Record<Freshness, string> = {
  live: "实时",
  fresh: "最新",
  stale: "数据已过期",
};


export const readinessLabel = (value: ReadinessStatus) => READINESS_LABELS[value];
export const readinessReasonLabel = (value: ReadinessStatus) => READINESS_REASONS[value];
export const runtimeFreshnessLabel = (value: RuntimeFreshness) => FRESHNESS_LABELS[value];
export const channelStatusLabel = (value: RuntimeChannelStatus) => CHANNEL_LABELS[value];
export const sourceFreshnessLabel = (value: Freshness) => SOURCE_FRESHNESS_LABELS[value];
