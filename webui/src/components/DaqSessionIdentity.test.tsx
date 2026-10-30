import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { SessionDetail, TurnDetail } from "../types";
import { SessionListItem } from "./SessionListItem";
import { SessionReplay } from "./session/SessionReplay";

const session = {
  session_key: "daq-opaque-key", agent_id: "ai-daq-fae-agent", source_kind: "fae",
  channel: "fae", title: "数采测试问题", created_at: "2026-10-30T00:00:00Z",
  last_active_at: "2026-10-30T00:00:00Z", turn_count: 1, feedback_count: 0,
  review_count: 0, latest_outcome: null, source_synced_at: null, participant_count: 1,
  freshness: "fresh", primary_sender_name: "可信数采员工",
  primary_sender_department: null, sender_identity_status: "name_only",
  verified_subject: {subject_id: "00000000-0000-4000-8000-000000000123", subject_type: "enterprise_member"},
  turns: [],
} as SessionDetail;

it("shows DAQ trusted questioner in both list and detail", () => {
  for (const html of [
    renderToStaticMarkup(<SessionListItem session={session} detailHref="/admin/sessions/daq-opaque-key" />),
    renderToStaticMarkup(<SessionReplay session={session} closureSummaries={{}} />),
  ]) {
    expect(html).toContain("可信数采员工");
    expect(html).toContain("内部用户");
  }
});

it("displays the same verified questioner on every question", () => {
  const turn = {
    turn_key: "turn-1", session_key: session.session_key, agent_id: session.agent_id,
    source_kind: "fae", turn_index: 0, question: "问题正文", answer: "答案正文",
    created_at: session.created_at, question_at: null, answer_at: null,
    sender_name: session.primary_sender_name, sender_department: null,
    verified_subject: session.verified_subject, sender_identity_status: "name_only",
    input_attachments: [], output_attachments: [], evidence: [], feedback: [], reviews: [], improvements: [],
  } as unknown as TurnDetail;
  const html = renderToStaticMarkup(<SessionReplay session={{...session, turns: [turn]}} closureSummaries={{}} />);
  expect(html.match(/可信数采员工/g)).toHaveLength(2);
});

it("keeps DAQ legacy ownership unknown and never presents a nickname as verified", () => {
  const unknown = {...session, verified_subject: null, primary_sender_name: "伪造昵称"};
  for (const html of [
    renderToStaticMarkup(<SessionListItem session={unknown} detailHref="/test" />),
    renderToStaticMarkup(<SessionReplay session={unknown} closureSummaries={{}} />),
  ]) {
    expect(html).toContain("提问人未知");
    expect(html).not.toContain("伪造昵称");
  }
});
