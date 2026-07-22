import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentDataSwitcher } from "./components/AgentDataSwitcher";
import { SessionListItem } from "./components/SessionListItem";
import type { AgentSummary, SessionSummary } from "./types";


const agents: AgentSummary[] = [
  {
    id: "hr-bot", name: "HR", domain: "HR", description: "People operations",
    glyph: "HR", accent: "people", source_kind: "metabot", deployment: "Local",
    visibility: "business",
    session_count: 10, total_turns: 20, last_activity_at: null,
    last_synced_at: null, freshness: "live",
  },
  {
    id: "ai-fae-agent", name: "AI FAE Agent", domain: "Technical Support",
    description: "Field application support", glyph: "FAE", accent: "support",
    visibility: "business",
    source_kind: "fae", deployment: "Alibaba Cloud", session_count: 168,
    total_turns: 236, last_activity_at: null, last_synced_at: null, freshness: "fresh",
  },
];

const session: SessionSummary = {
  session_key: "fae:one", agent_id: "ai-fae-agent", source_kind: "fae",
  channel: "fae", title: "Original question", created_at: "2026-07-21T08:00:00Z",
  last_active_at: "2026-07-21T09:00:00Z", turn_count: 2, feedback_count: 4,
  review_count: 1, latest_outcome: null, source_synced_at: "2026-07-21T09:10:00Z",
  freshness: "fresh",
};


describe("Agent data browser", () => {
  it("renders every Agent and exposes the selected Agent state", () => {
    const html = renderToStaticMarkup(
      <AgentDataSwitcher agents={agents} selectedId="ai-fae-agent" onSelect={() => undefined} />,
    );

    expect(html).toContain("HR");
    expect(html).toContain("AI FAE Agent");
    expect(html).toContain("METABOT");
    expect(html).toContain("FAE");
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain('data-agent-id="ai-fae-agent"');
  });

  it("uses Agent Sessions instead of generic Flywheel metrics", () => {
    const source = readFileSync(new URL("./pages/FlywheelPage.tsx", import.meta.url), "utf8");

    expect(source).toContain("AgentDataSwitcher");
    expect(source).toContain("fetchSessions");
    expect(source).toContain("businessAgents");
    for (const rejected of [
      "fetchFlywheelOverview", "fetchFlywheelItems", "Feedback", "Pending Review",
      "Eval Candidates", "Daily sync",
    ]) expect(source).not.toContain(rejected);
  });

  it("hides source-specific Feedback and Review badges in the common browser", () => {
    const html = renderToStaticMarkup(<SessionListItem session={session} showSignals={false} />);

    expect(html).toContain("2 turns");
    expect(html).not.toContain("feedback");
    expect(html).not.toContain("review");
  });
});
