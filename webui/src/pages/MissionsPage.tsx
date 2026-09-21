import { useEffect, useRef, useState } from "react";

import { platformPath } from "../auth";
import { ConversationApiError, listConversations } from "../conversationApi";
import type { Conversation, ConversationStatus } from "../conversationTypes";
import { EmptyState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { professionalAgentLabel } from "../components/conversation/agentLabels";

type ReadError = "unavailable" | "unauthenticated" | "forbidden";
function readError(error: unknown): ReadError {
  if (error instanceof ConversationApiError && error.status === 401) return "unauthenticated";
  if (error instanceof ConversationApiError && error.status === 403) return "forbidden";
  return "unavailable";
}
function statusLabel(status: Conversation["activity_status"]): string | null {
  if (!status) return null;
  const labels: Record<string, string> = {
    accepted: "排队中", running: "处理中", waiting_agents: "执行中", waiting_user: "需要补充",
    waiting_confirmation: "待确认", completing: "整理中", completed: "已完成", failed: "未完成",
    cancelled: "已停止", interrupted: "已中断", partially_completed: "部分完成",
  };
  return labels[status] ?? null;
}
function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export function MissionsPage() {
  const [status, setStatus] = useState<ConversationStatus>("active");
  return <div className="missions-page">
    <header className="use-page-intro"><h1>历史任务</h1></header>
    <nav className="history-filter" aria-label="历史范围">
      <button type="button" aria-pressed={status === "active"} onClick={() => setStatus("active")}>最近</button>
      <button type="button" aria-pressed={status === "archived"} onClick={() => setStatus("archived")}>已归档</button>
    </nav>
    <HistoryList key={status} status={status} />
  </div>;
}

function HistoryList({ status }: { status: ConversationStatus }) {
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ReadError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    setError(null); setItems(null); setCursor(null);
    void listConversations(controller.signal, undefined, 20, undefined, status).then(page => {
      if (controller.signal.aborted) return;
      setItems(page.items); setCursor(page.next_cursor);
    }).catch(failure => {
      if (!controller.signal.aborted) setError(readError(failure));
    }).finally(() => { if (request.current === controller) request.current = null; });
    return () => { controller.abort(); request.current?.abort(); request.current = null; };
  }, [attempt, status]);

  const more = async () => {
    if (!cursor || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoadingMore(true); setError(null);
    try {
      const page = await listConversations(controller.signal, cursor, 20, undefined, status);
      if (controller.signal.aborted) return;
      setItems(current => [...new Map([...(current ?? []), ...page.items].map(item => [item.conversation_id, item])).values()]);
      setCursor(page.next_cursor);
    } catch (failure) {
      if (controller.signal.aborted) return;
      const kind = readError(failure);
      setError(kind);
      if (kind !== "unavailable") { setItems(null); setCursor(null); }
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
      if (request.current === controller) request.current = null;
    }
  };

  if (error && items === null) return <section className="data-state data-error" role="alert">
    <strong>{error === "unauthenticated" ? "请重新登录" : error === "forbidden" ? "无权读取历史任务" : "历史任务暂时无法读取"}</strong>
    {error === "unauthenticated" ? <a href={platformPath("/login?return_path=%2Fmissions")}>重新登录</a>
      : error === "forbidden" ? <p>请联系苍渊。</p>
      : <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button>}
  </section>;
  if (items === null) return <LoadingState label="正在读取历史任务" />;
  if (items.length === 0) return <EmptyState title={status === "archived" ? "暂无归档任务" : "还没有历史任务"} description="" />;
  return <>
    <div className="mission-history-list">{items.map(item => <PlatformLink href={`/conversations/${encodeURIComponent(item.conversation_id)}`} key={item.conversation_id}>
      <div><span>{item.mode === "brain" ? "AI 助手" : professionalAgentLabel(item.direct_agent_id)}</span><strong>{item.title}</strong></div>
      <p>{statusLabel(item.activity_status) && <b>{statusLabel(item.activity_status)}</b>}<time dateTime={item.updated_at}>{timeLabel(item.updated_at)}</time></p>
    </PlatformLink>)}</div>
    {error && <p className="mission-more-error" role="alert">更早任务暂时无法读取。</p>}
    {cursor && <button className="mission-load-more" disabled={loadingMore} onClick={() => void more()} type="button">
      {loadingMore ? "正在读取…" : error ? "重试加载" : "加载更早任务"}
    </button>}
  </>;
}
