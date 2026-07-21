import { useEffect, useState } from "react";

import { fetchSession } from "../api";
import { ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { TurnCard } from "../components/TurnCard";
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
  if (!session) return <LoadingState label="Loading Session" />;
  return <>
    <PlatformLink className="back-link" href="/sessions">← All Sessions</PlatformLink>
    <section className="session-detail-head">
      <div><p className="eyebrow">SESSION REPLAY</p><h1>{session.title || "Untitled Session"}</h1><code>{session.session_key}</code></div>
      <div className="session-detail-source"><span>{session.source_kind.toUpperCase()}</span><strong>{session.channel}</strong><small className={`freshness freshness-${session.freshness}`}>{session.freshness}</small></div>
      <dl><div><dt>Agent</dt><dd><PlatformLink href={`/agents/${encodeURIComponent(session.agent_id)}`}>{session.agent_id}</PlatformLink></dd></div><div><dt>Turns</dt><dd>{session.turn_count}</dd></div><div><dt>Feedback</dt><dd>{session.feedback_count}</dd></div><div><dt>Reviews</dt><dd>{session.review_count}</dd></div></dl>
    </section>
    <section className="turn-stack">{session.turns.map((turn) => <TurnCard turn={turn} key={turn.turn_key} />)}</section>
  </>;
}
