import { useEffect, useState } from "react";

import { fetchSession } from "../api";
import { ErrorState, LoadingState } from "../components/DataState";
import { sourceFreshnessLabel } from "../copy";
import { PlatformLink } from "../components/PlatformLink";
import { TurnCard } from "../components/TurnCard";
import { sessionReturnTarget } from "../navigationContext";
import type { SessionDetail } from "../types";


export function SessionDetailPage({ sessionKey }: { sessionKey: string }) {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetchSession(sessionKey, controller.signal).then(setSession).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [sessionKey]);
  if (error) return <ErrorState />;
  if (!session) return <LoadingState label="正在加载 Session" />;
  const returnTarget = sessionReturnTarget(window.history.state);
  return <>
    <PlatformLink className="back-link" href={returnTarget ?? "/sessions"} onClick={(event) => {
      if (!returnTarget || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      window.history.back();
    }}>{returnTarget ? "← 返回" : "← 返回 Session 列表"}</PlatformLink>
    <section className="session-detail-head">
      <div><p>Session 回放</p><h1>{session.title || "未命名 Session"}</h1><code>{session.session_key}</code></div>
      <div className="session-detail-source"><span>{session.source_kind.toUpperCase()}</span><strong>{session.channel}</strong><small className={`freshness freshness-${session.freshness}`}>{sourceFreshnessLabel(session.freshness)}</small></div>
      <dl><div><dt>Agent</dt><dd><PlatformLink href={`/agents/${encodeURIComponent(session.agent_id)}`}>{session.agent_id}</PlatformLink></dd></div><div><dt>对话轮次</dt><dd>{session.turn_count}</dd></div><div><dt>反馈</dt><dd>{session.feedback_count}</dd></div><div><dt>复审</dt><dd>{session.review_count}</dd></div></dl>
    </section>
    <section className="turn-stack">{session.turns.map((turn) => <TurnCard turn={turn} key={turn.turn_key} />)}</section>
  </>;
}
