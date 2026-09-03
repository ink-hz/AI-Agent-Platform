import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { faeWorkbenchApi } from "../faeWorkbenchApi";
import type { SessionSummary } from "../types";
import { FAE_MANAGEMENT_PATH } from "../platform/workspaces";
import { SessionsView } from "./SessionsPage";


const FAE_SESSIONS_PATH = `${FAE_MANAGEMENT_PATH}/sessions`;
const faeDetailHref = (session: SessionSummary) => `${FAE_SESSIONS_PATH}/${encodeURIComponent(session.session_key)}`;


export function FaeSessionsPage() {
  return <FaeWorkbenchShell currentSection="sessions">
    <SessionsView
      basePath={FAE_SESSIONS_PATH}
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
        date_before: query.date_before,
        subject_key: query.subject_key,
        has_subject: query.has_subject,
        abnormal: query.abnormal,
        has_latency: query.has_latency,
        limit: query.limit,
        offset: query.offset,
      }, signal)}
      detailHref={faeDetailHref}
    />
  </FaeWorkbenchShell>;
}
