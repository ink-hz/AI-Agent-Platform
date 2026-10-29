import { sourceFreshnessLabel } from "../copy";
import type { AgentSourceStatus, Freshness } from "../types";

const MODE_LABELS = { cloud_direct: "云端直连", office: "本地发布", native: "云端原生" };

export function SourceFreshness({ freshness, status }: { freshness: Freshness; status?: AgentSourceStatus | null }) {
  const title = status ? [
    `来源读取：${status.last_source_success_at ?? "暂无成功证据"}`,
    `副本发布：${status.last_published_at ?? "暂无记录"}`,
    `观测滞后：${status.lag_seconds === null ? "未知" : `${status.lag_seconds}s`}`,
    ...(status.last_failure_at ? [`最近失败：${status.last_failure_at}`] : []),
  ].join("；") : undefined;
  return <span className={`freshness freshness-${freshness}`} title={title}>
    {status && `${MODE_LABELS[status.mode]} · `}
    {status?.mode === "native" ? "无需同步" : sourceFreshnessLabel(freshness)}
    {status?.last_error && ` · ${status.last_error}`}
  </span>;
}
