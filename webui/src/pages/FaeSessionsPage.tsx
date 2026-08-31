import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { faeWorkbenchApi } from "../faeWorkbenchApi";
import type { SessionSummary } from "../types";
import { SessionsView } from "./SessionsPage";


const faeDetailHref = (session: SessionSummary) => `/admin/fae/sessions/${encodeURIComponent(session.session_key)}`;


export function FaeSessionsPage() {
  return <FaeWorkbenchShell currentSection="sessions">
    <SessionsView
      basePath="/admin/fae/sessions"
      title="FAE Sessions"
      description="查看 FAE 范围内的真实 Session 和反馈复审信号。"
      showScopeFilters={false}
      load={(query, signal) => faeWorkbenchApi.listSessions({
        q: query.q,
        channel: query.channel,
        sentiment: query.sentiment === "positive" || query.sentiment === "negative" || query.sentiment === "other"
          ? query.sentiment
          : undefined,
        review_status: query.review_status,
        outcome: query.outcome,
        date_from: query.date_from,
        date_to: query.date_to,
        limit: query.limit,
        offset: query.offset,
      }, signal)}
      detailHref={faeDetailHref}
    />
  </FaeWorkbenchShell>;
}
