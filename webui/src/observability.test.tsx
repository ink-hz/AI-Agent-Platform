import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentDirectoryCard } from "./components/AgentDirectoryCard";
import { SessionListItem } from "./components/SessionListItem";
import type { AgentSummary, SessionSummary } from "./types";


const agent: AgentSummary = {
  id: "ai-fae-agent",
  name: "AI FAE",
  domain: "Field Application Engineering",
  description: "Production engineering Agent",
  glyph: "FAE",
  accent: "cyan",
  source_kind: "fae",
  deployment: "Alibaba Cloud",
  session_count: 168,
  total_turns: 236,
  last_activity_at: "2026-07-21T09:00:00Z",
  last_synced_at: "2026-07-21T09:10:00Z",
  freshness: "fresh",
};


const session: SessionSummary = {
  session_key: "fae:session-1",
  agent_id: "ai-fae-agent",
  source_kind: "fae",
  channel: "DingTalk",
  title: "Gemini 335L 如何排查？",
  created_at: "2026-07-21T08:00:00Z",
  last_active_at: "2026-07-21T09:00:00Z",
  turn_count: 3,
  feedback_count: 1,
  review_count: 0,
  latest_outcome: "resolved",
  source_synced_at: "2026-07-21T09:10:00Z",
  freshness: "fresh",
};


describe("observability directory components", () => {
  it("renders Agent source, deployment, Sessions, and Conversations", () => {
    const html = renderToStaticMarkup(<AgentDirectoryCard agent={agent} />);

    expect(html).toContain("AI FAE");
    expect(html).toContain("Alibaba Cloud");
    expect(html).toContain("168");
    expect(html).toContain("236");
    expect(html).toContain("/agents/ai-fae-agent");
  });

  it("preserves original Session language and links encoded keys", () => {
    const html = renderToStaticMarkup(<SessionListItem session={session} />);

    expect(html).toContain("Gemini 335L 如何排查？");
    expect(html).toContain("DingTalk");
    expect(html).toContain("3 turns");
    expect(html).toContain("/sessions/fae%3Asession-1");
  });
});
