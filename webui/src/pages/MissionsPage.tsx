import { useEffect, useState } from "react";

import { listMissions } from "../brainApi";
import type { Mission } from "../brainTypes";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";


function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    planning: "分析中", delegated: "执行中", synthesizing: "整理中", completed: "已完成",
    partially_completed: "部分完成", failed: "未完成", cancelled: "已停止", interrupted: "已中断",
  };
  return labels[status] ?? "处理中";
}

function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export function MissionsPage() {
  const [items, setItems] = useState<Mission[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    listMissions(controller.signal).then((page) => { setItems(page.items); setCursor(page.next_cursor); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [attempt]);

  const more = async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listMissions(undefined, cursor);
      setItems((current) => [...(current ?? []), ...page.items]);
      setCursor(page.next_cursor);
    } catch {
      setError(true);
    } finally {
      setLoadingMore(false);
    }
  };

  return <div className="missions-page">
    <section className="use-page-intro"><p>YOUR WORK</p><h1>历史任务</h1><span>继续查看 Agent 大脑和专业 Agent 已保存的任务。</span></section>
    {error && items === null ? <ErrorState onRetry={() => setAttempt((value) => value + 1)} />
      : items === null ? <LoadingState label="正在读取历史任务" />
      : items.length === 0 ? <EmptyState title="还没有历史任务" description="从 Agent 大脑发起第一个任务。" />
      : <div className="mission-history-list">{items.map((mission) => <PlatformLink href={`/missions/${encodeURIComponent(mission.mission_id)}`} key={mission.mission_id}>
        <div><span>{mission.mode === "direct_agent" ? mission.direct_agent_id ?? "专业 Agent" : "Agent 大脑"}</span><strong>{mission.prompt}</strong></div>
        <p><b>{statusLabel(mission.status)}</b><time dateTime={mission.updated_at}>{timeLabel(mission.updated_at)}</time></p>
      </PlatformLink>)}</div>}
    {cursor && <button className="mission-load-more" disabled={loadingMore} onClick={() => void more()} type="button">{loadingMore ? "正在读取…" : "加载更早任务"}</button>}
    {error && items !== null && <p className="mission-more-error" role="alert">更早任务暂时无法读取，请稍后重试。</p>}
  </div>;
}
