import type { SessionSummary } from "../types";
import { PlatformLink } from "./PlatformLink";


function dateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("en-GB", {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}


export function SessionListItem({ session }: { session: SessionSummary }) {
  return (
    <PlatformLink className="session-row" href={`/sessions/${encodeURIComponent(session.session_key)}`}>
      <div className="session-source"><span>{session.source_kind.toUpperCase()}</span><b>{session.channel}</b></div>
      <div className="session-title"><strong>{session.title || "Untitled Session"}</strong><span>{session.agent_id}</span></div>
      <div className="session-counts"><span>{session.turn_count} turns</span>{session.feedback_count > 0 && <span>{session.feedback_count} feedback</span>}{session.review_count > 0 && <span>{session.review_count} review</span>}</div>
      <time dateTime={session.last_active_at}>{dateTime(session.last_active_at)}</time>
      <span className={`freshness freshness-${session.freshness}`}>{session.freshness}</span>
    </PlatformLink>
  );
}
