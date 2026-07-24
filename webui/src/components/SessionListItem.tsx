import { sourceFreshnessLabel } from "../copy";
import type { SessionSummary } from "../types";
import { additionalParticipantLabel, formatSenderIdentity } from "../senderIdentity";
import { PlatformLink } from "./PlatformLink";


function dateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(date);
}


export function SessionListItem({
  session,
  showSignals = true,
}: {
  session: SessionSummary;
  showSignals?: boolean;
}) {
  const sender = session.source_kind === "metabot"
    ? formatSenderIdentity(session.primary_sender_name, session.primary_sender_department)
    : null;
  const additionalParticipants = additionalParticipantLabel(session.participant_count);
  return (
    <PlatformLink className="session-row" href={`/sessions/${encodeURIComponent(session.session_key)}`} preserveSessionContext>
      <div className="session-source"><span>{session.source_kind.toUpperCase()}</span><b>{session.channel}</b></div>
      <div className="session-title">
        <strong>{session.title || "未命名 Session"}</strong>
        <span>{session.agent_id}</span>
        {sender && <small className="session-sender"><b>{sender}</b>{additionalParticipants && <em>{additionalParticipants}</em>}</small>}
      </div>
      <div className="session-counts"><span>{session.turn_count} 轮</span>{showSignals && session.feedback_count > 0 && <span>{session.feedback_count} 条反馈</span>}{showSignals && session.review_count > 0 && <span>{session.review_count} 条复审</span>}</div>
      <time dateTime={session.last_active_at}>{dateTime(session.last_active_at)}</time>
      <span className={`freshness freshness-${session.freshness}`}>{sourceFreshnessLabel(session.freshness)}</span>
    </PlatformLink>
  );
}
