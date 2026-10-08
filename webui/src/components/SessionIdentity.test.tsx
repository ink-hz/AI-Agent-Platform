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
