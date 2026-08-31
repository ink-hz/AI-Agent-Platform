import { fetchSession } from "../api";
import { ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { SessionReplay } from "../components/session/SessionReplay";
import { useSessionDetail } from "../components/session/useSessionDetail";
import { sessionReturnTarget } from "../navigationContext";


export function SessionDetailPage({ sessionKey }: { sessionKey: string }) {
  const { session, closureSummaries, error } = useSessionDetail(fetchSession, sessionKey, "negative-only");
  if (error) return <ErrorState />;
  if (!session) return <LoadingState label="正在加载 Session" />;
  const returnTarget = sessionReturnTarget(window.history.state);
  return <>
    <PlatformLink className="back-link" href={returnTarget ?? "/admin/sessions"} onClick={(event) => {
      if (!returnTarget || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      window.history.back();
    }}>{returnTarget ? "← 返回" : "← 返回 Session 列表"}</PlatformLink>
    <SessionReplay session={session} closureSummaries={closureSummaries} />
  </>;
}
