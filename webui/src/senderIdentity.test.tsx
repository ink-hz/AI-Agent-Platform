import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SessionListItem } from "./components/SessionListItem";
import { TurnCard } from "./components/TurnCard";
import { formatSenderIdentity } from "./senderIdentity";
import type { SessionSummary, TurnDetail } from "./types";


const session: SessionSummary = {
  session_key: "metabot:marketing-bot:one",
  agent_id: "marketing-prospecting-bot",
  source_kind: "metabot",
  channel: "feishu",
  title: "New campaign",
  created_at: "2026-07-23T08:00:00Z",
  last_active_at: "2026-07-23T09:00:00Z",
  turn_count: 4,
  feedback_count: 0,
  review_count: 0,
  latest_outcome: null,
  source_synced_at: null,
  freshness: "live",
  participant_count: 3,
  primary_sender_name: "Lina",
  primary_sender_department: "Marketing",
  sender_identity_status: "resolved",
};

const turn: TurnDetail = {
  turn_key: "metabot:marketing-bot:turn-one",
  session_key: session.session_key,
  agent_id: session.agent_id,
  source_kind: "metabot",
  turn_index: 1,
  question: "Draft a campaign plan",
  answer: "Here is the plan.",
  created_at: "2026-07-23T08:00:00Z",
  trace_key: null,
  outcome: null,
  fallback_used: false,
  duration_ms: null,
  sources: [],
  evidence: [],
  evidence_availability: "unavailable",
  feedback: [],
  reviews: [],
  improvements: [],
  details: {},
  sender_name: "Lina",
  sender_department: null,
  sender_identity_status: "name_only",
};


describe("sender identity presentation", () => {
  it("formats only safe presentation fields", () => {
    expect(formatSenderIdentity("Lina", "Marketing")).toBe("Lina · Marketing");
    expect(formatSenderIdentity("Lina", null)).toBe("Lina · 部门未记录");
    expect(formatSenderIdentity(null, null)).toBe("Feishu 用户");
  });

  it("shows the primary sender and group participation on MetaBot Sessions", () => {
    const html = renderToStaticMarkup(<SessionListItem session={session} />);

    expect(html).toContain("Lina · Marketing");
    expect(html).toContain("另有 2 人");
    expect(html).not.toMatch(/open_id|union_id|staff_id/i);
  });

  it("shows the sender next to each captured question", () => {
    const html = renderToStaticMarkup(<TurnCard turn={turn} />);

    expect(html).toContain("Lina · 部门未记录");
    expect(html).toContain("Draft a campaign plan");
  });

  it("does not invent identity UI for sources without compatible data", () => {
    const html = renderToStaticMarkup(<SessionListItem session={{
      ...session,
      source_kind: "fae",
      participant_count: null,
      primary_sender_name: null,
      primary_sender_department: null,
      sender_identity_status: "unavailable",
    }} />);

    expect(html).not.toContain("Feishu 用户");
    expect(html).not.toContain("部门未记录");
  });
});
