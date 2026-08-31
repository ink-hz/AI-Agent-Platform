import { ErrorState, LoadingState } from "../components/DataState";
import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { PlatformLink } from "../components/PlatformLink";
import { SessionReplay } from "../components/session/SessionReplay";
import { useSessionDetail } from "../components/session/useSessionDetail";
import { sourceFreshnessLabel } from "../copy";
import { faeWorkbenchApi } from "../faeWorkbenchApi";
import type { SessionDetail } from "../types";


const loadFaeSession = (sessionKey: string, signal: AbortSignal) => faeWorkbenchApi.session(sessionKey, signal);


function identityLabel(session: SessionDetail): string {
  const name = session.primary_sender_name?.trim();
  if (session.sender_identity_status === "unavailable" || !name) return "身份信息暂不可用";
  const department = session.primary_sender_department?.trim();
  return department ? `${name} · ${department}` : name;
}


function governanceHref(sessionKey: string, turnKey: string): string {
  const params = new URLSearchParams({ session_key: sessionKey, turn_key: turnKey });
  return `/admin/fae/issues?${params}`;
}


function GovernancePanel({ session }: { session: SessionDetail }) {
  return <aside className="fae-session-governance" aria-label="Session 治理信息">
    <div><p>GOVERNANCE</p><h2>治理信息</h2></div>
    <dl>
      <div><dt>渠道</dt><dd>{session.channel}</dd></div>
      <div><dt>Outcome</dt><dd>{session.latest_outcome || "暂不可用"}</dd></div>
      <div><dt>Turn 数</dt><dd>{session.turn_count}</dd></div>
      <div><dt>反馈数</dt><dd>{session.feedback_count}</dd></div>
      <div><dt>复审数</dt><dd>{session.review_count}</dd></div>
      <div><dt>身份</dt><dd>{identityLabel(session)}</dd></div>
      <div><dt>数据截止时间</dt><dd>{session.source_synced_at
        ? <time dateTime={session.source_synced_at}>{session.source_synced_at}</time>
        : "暂不可用"}</dd></div>
      <div><dt>数据新鲜度</dt><dd><span className={`freshness freshness-${session.freshness}`}>{sourceFreshnessLabel(session.freshness)}</span></dd></div>
    </dl>
  </aside>;
}


export function FaeSessionDetailPage({ sessionKey }: { sessionKey: string }) {
  const { session, closureSummaries, error } = useSessionDetail(loadFaeSession, sessionKey, "all-turns");
  let content;
  if (error) content = <ErrorState />;
  else if (!session) content = <LoadingState label="正在加载 Session" />;
  else content = <>
    <PlatformLink className="back-link" href="/admin/fae/sessions">← 返回 FAE Sessions</PlatformLink>
    <div className="fae-session-detail-layout">
      <div><SessionReplay
        session={session}
        closureSummaries={closureSummaries}
        governanceHref={(turn) => governanceHref(session.session_key, turn.turn_key)}
      /></div>
      <GovernancePanel session={session} />
    </div>
  </>;
  return <FaeWorkbenchShell currentSection="sessions">{content}</FaeWorkbenchShell>;
}
