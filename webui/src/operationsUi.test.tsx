import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DailyBrief } from "./components/DailyBrief";
import { OperationalEventItem } from "./components/OperationalEventItem";
import type { OperationsBrief } from "./types";


const briefFixture: OperationsBrief = {
  period_start: "2026-07-21T10:00:00Z",
  period_end: "2026-07-22T10:00:00Z",
  freshness: { status: "current", evaluated_at: "2026-07-22T10:00:00Z", failed_groups: [] },
  can_claim_healthy: false,
  attention: [{
    event_id: "runtime:ai-fae-agent",
    agent_id: "ai-fae-agent",
    agent_visibility: "business",
    event_type: "runtime_offline",
    event_family: "runtime",
    severity: "critical",
    status: "active",
    title: "AI FAE Agent is offline",
    summary: "Two consecutive runtime observations",
    source_kind: "runtime",
    occurred_at: "2026-07-22T09:30:00Z",
    first_observed_at: "2026-07-22T09:25:00Z",
    last_observed_at: "2026-07-22T09:30:00Z",
    resolved_at: null,
    facts: { consecutive_observations: 2 },
    target_kind: "agent",
    target_id: "ai-fae-agent",
    target_path: "/agents/ai-fae-agent",
    fingerprint: "runtime:ai-fae-agent:offline",
  }],
  usage: {
    conversations: 33,
    active_agents: 6,
    leaders: [{ agent_id: "ai-fae-agent", agent_name: "AI FAE Agent", conversations: 12 }],
  },
  changes: [{
    event_id: "recovery:ai-fae-agent",
    agent_id: "ai-fae-agent",
    agent_visibility: "business",
    event_type: "runtime_recovered",
    event_family: "recovery",
    severity: "info",
    status: "historical",
    title: "AI FAE Agent recovered",
    summary: "Runtime observations returned to normal",
    source_kind: "runtime",
    occurred_at: "2026-07-22T08:30:00Z",
    first_observed_at: "2026-07-22T08:30:00Z",
    last_observed_at: "2026-07-22T08:30:00Z",
    resolved_at: "2026-07-22T08:30:00Z",
    facts: {},
    target_kind: "agent",
    target_id: "ai-fae-agent",
    target_path: "/agents/ai-fae-agent",
    fingerprint: "recovery:ai-fae-agent:runtime",
  }],
};

const partialFreshness: OperationsBrief["freshness"] = {
  status: "partial",
  evaluated_at: "2026-07-22T10:00:00Z",
  failed_groups: ["execution"],
};


describe("DailyBrief", () => {
  it("renders evidence-backed Attention and Last 24 Hours", () => {
    const html = renderToStaticMarkup(<DailyBrief brief={briefFixture} />);
    expect(html).toContain("Needs Attention");
    expect(html).toContain("Last 24 Hours");
    expect(html).toContain("AI FAE Agent is offline");
    expect(html).toContain("Two consecutive runtime observations");
    expect(html).toContain("View all activity");
  });

  it("does not claim health for partial evaluation", () => {
    const html = renderToStaticMarkup(
      <DailyBrief brief={{ ...briefFixture, freshness: partialFreshness, can_claim_healthy: false, attention: [] }} />,
    );
    expect(html).not.toContain("No critical issues");
    expect(html).toContain("Brief partially evaluated");
  });

  it("does not claim health when last-known data is locally stale", () => {
    const html = renderToStaticMarkup(
      <DailyBrief brief={{ ...briefFixture, can_claim_healthy: true, attention: [] }} stale />,
    );
    expect(html).not.toContain("No critical issues");
    expect(html).toContain("Brief data is stale");
  });

  it("does not present resolved events as active Attention", () => {
    const html = renderToStaticMarkup(
      <DailyBrief brief={{
        ...briefFixture,
        can_claim_healthy: true,
        attention: [{ ...briefFixture.attention[0], status: "resolved" }],
      }} />,
    );

    expect(html).not.toContain("AI FAE Agent is offline");
    expect(html).toContain("No critical issues");
  });

  it("limits Overview changes to five and summarizes fleet usage", () => {
    const changes = Array.from({ length: 6 }, (_, index) => ({
      ...briefFixture.changes[0],
      event_id: `change-${index}`,
      title: `Change ${index + 1}`,
    }));
    const html = renderToStaticMarkup(<DailyBrief brief={{ ...briefFixture, changes }} />);

    expect(html).toContain("33 new conversations across 6 Business Agents");
    expect(html).toContain("Change 5");
    expect(html).not.toContain("Change 6");
  });

  it("renders severity with an icon and readable text", () => {
    const html = renderToStaticMarkup(<OperationalEventItem event={briefFixture.attention[0]} />);

    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("Critical");
    expect(html).toContain('href="/agents/ai-fae-agent"');
  });
});
