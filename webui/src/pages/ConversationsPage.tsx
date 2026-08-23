import { useEffect, useState } from "react";

import { listConversations } from "../conversationApi";
import type { Conversation, ConversationPage } from "../conversationTypes";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";


function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}


function merge(current: Conversation[], incoming: Conversation[]): Conversation[] {
  return [...new Map([...current, ...incoming].map((item) => [item.conversation_id, item])).values()];
}


export function ConversationsPage({
  list = listConversations,
}: {
  list?: (signal?: AbortSignal, before?: string) => Promise<ConversationPage>;
}) {
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    void list(controller.signal).then((page) => {
      if (!controller.signal.aborted) { setItems(page.items); setCursor(page.next_cursor); }
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [attempt, list]);

  const more = async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await list(undefined, cursor);
      setItems((current) => merge(current ?? [], page.items));
      setCursor(page.next_cursor);
    } catch {
      setError(true);
    } finally {
      setLoadingMore(false);
    }
  };

  return <div className="conversations-page">
    <section className="conversation-history-header">
      <div><p>YOUR WORK</p><h1>历史对话</h1><span>每条记录是一段可以继续追问的完整对话。</span></div>
      <PlatformLink href="/">＋ 新建对话</PlatformLink>
    </section>
    {error && items === null ? <ErrorState onRetry={() => setAttempt((value) => value + 1)} />
      : items === null ? <LoadingState label="正在读取历史对话" />
      : items.length === 0 ? <EmptyState title="还没有历史对话" description="从 Agent 大脑开始第一段对话。" />
      : <div className="conversation-history-list">{items.map((item) => <PlatformLink
        href={`/conversations/${encodeURIComponent(item.conversation_id)}`}
        key={item.conversation_id}
      >
        <div><span>{item.mode === "direct_agent" ? item.direct_agent_id ?? "专业 Agent" : "Agent 大脑"}</span><strong>{item.title}</strong></div>
        <p><b>{item.status === "archived" ? "已归档" : "可继续"}</b><time dateTime={item.updated_at}>{timeLabel(item.updated_at)}</time></p>
      </PlatformLink>)}</div>}
    {cursor && <button className="conversation-load-more" disabled={loadingMore} onClick={() => void more()} type="button">{loadingMore ? "正在读取…" : "加载更早对话"}</button>}
    {error && items !== null && <p className="conversation-action-error" role="alert">更早对话暂时无法读取，请稍后重试。</p>}
  </div>;
}
