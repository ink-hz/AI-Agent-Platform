import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import type { SessionDetail, SessionSummary } from "../types";
import { SessionListItem } from "./SessionListItem";
import { SessionReplay } from "./session/SessionReplay";


const adminSession: SessionSummary = {
  session_key: "admin:verified-session",
  agent_id: "ai-admin-agent",
  source_kind: "admin",
  channel: "admin",
  title: "会议室怎么预约？",
  created_at: "2026-10-08T08:00:00Z",
  last_active_at: "2026-10-08T08:01:00Z",
  turn_count: 1,
  feedback_count: 0,
  review_count: 0,
  latest_outcome: "resolved",
  source_synced_at: "2026-10-08T08:02:00Z",
  freshness: "fresh",
  participant_count: null,
  primary_sender_name: "测试员工",
  primary_sender_department: null,
  sender_identity_status: "name_only",
};


it("shows the verified Admin questioner in session list and replay", () => {
  const list = renderToStaticMarkup(
    <SessionListItem session={adminSession} detailHref="/admin/sessions/admin%3Averified-session" />,
  );
  const detail = renderToStaticMarkup(
    <SessionReplay session={{ ...adminSession, turns: [] } as SessionDetail} closureSummaries={{}} />,
  );

  expect(list).toContain("测试员工");
  expect(detail).toContain("提问人");
  expect(detail).toContain("测试员工");
});


it("does not invent a questioner for an unresolved Admin session", () => {
  const list = renderToStaticMarkup(
    <SessionListItem session={{ ...adminSession, primary_sender_name: null, sender_identity_status: "unavailable" }} detailHref="/admin/sessions/admin%3Aunresolved" />,
  );
  expect(list).not.toContain("测试员工");
});


it.each([['enterprise_member', '内部用户'], ['partner_operator', '外部用户']] as const)("shows trusted FAE %s identity in list and replay", (subject_type, label) => {
  const session = { ...adminSession, source_kind: 'fae', agent_id: 'ai-fae-agent',
    verified_subject: {subject_id: '00000000-0000-4000-8000-000000000123', subject_type},
  } as SessionSummary;
  for (const html of [
    renderToStaticMarkup(<SessionListItem session={session} detailHref="/admin/sessions/test" />),
    renderToStaticMarkup(<SessionReplay session={{...session, turns: []}} closureSummaries={{}} />),
  ]) {
    expect(html).toContain('测试员工');
    expect(html).toContain(label);
  }
});


it("keeps legacy FAE identity unknown and verified missing names distinguishable", () => {
  const session = {...adminSession, source_kind: 'fae', agent_id: 'ai-fae-agent', primary_sender_name: null} as SessionSummary;
  const legacy = renderToStaticMarkup(<SessionListItem session={session} detailHref="/test" />);
  expect(legacy).toContain('提问人未知');
  expect(legacy).not.toContain('匿名');
  const verified = {...session, verified_subject: {subject_id: '00000000-0000-4000-8000-000000000123', subject_type: 'partner_operator'}} as SessionSummary;
  const html = renderToStaticMarkup(<SessionReplay session={{...verified, turns: []}} closureSummaries={{}} />);
  expect(html).toContain('外部用户');
  expect(html).toContain('姓名暂不可用');
  expect(html).toContain('00000000-0000-4000-8000-000000000123');
});
