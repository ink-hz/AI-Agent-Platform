import { describe, expect, it } from "vitest";

import {
  applyOperationsFailure,
  applyOperationsSuccess,
  briefStatusLabel,
  eventTargetPath,
  eventTimeLabel,
  initialOperationsState,
} from "./operations";
import type { OperationalEvent, OperationsBrief } from "./types";


const event: OperationalEvent = {
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
  occurred_at: "2026-07-22T10:30:00Z",
  first_observed_at: "2026-07-22T10:25:00Z",
  last_observed_at: "2026-07-22T10:30:00Z",
  resolved_at: null,
  facts: { consecutive_observations: 2 },
  target_kind: "agent",
  target_id: "ai-fae-agent",
  target_path: "/agents/ai-fae-agent",
  fingerprint: "runtime:ai-fae-agent:offline",
};

const brief: OperationsBrief = {
  period_start: "2026-07-21T10:00:00Z",
  period_end: "2026-07-22T10:00:00Z",
  freshness: { status: "current", evaluated_at: "2026-07-22T10:00:00Z", failed_groups: [] },
  can_claim_healthy: false,
  attention: [event],
  usage: { conversations: 8, active_agents: 2, leaders: [] },
  changes: [],
};


describe("Operations Brief presentation helpers", () => {
  it("labels evaluation freshness without claiming health", () => {
    expect(briefStatusLabel(brief.freshness)).toBe("Evaluated 18:00");
    expect(briefStatusLabel({ ...brief.freshness, status: "partial" })).toBe("Brief partially evaluated · 18:00");
    expect(briefStatusLabel(brief.freshness, true)).toBe("Brief data is stale · Last evaluated 18:00");
    expect(briefStatusLabel({ status: "unavailable", evaluated_at: null, failed_groups: [] })).toBe("Brief unavailable");
  });

  it("formats event times in the product timezone", () => {
    expect(eventTimeLabel(event)).toBe("22 Jul, 18:30");
  });

  it("allows only target paths handled by the existing SPA", () => {
    expect(eventTargetPath(event)).toBe("/agents/ai-fae-agent");
    expect(eventTargetPath({ ...event, target_path: "/sessions/fae%3Asession-2" })).toBe("/sessions/fae%3Asession-2");
    expect(eventTargetPath({ ...event, target_path: "https://example.com/admin" })).toBeNull();
    expect(eventTargetPath({ ...event, target_path: "/api/cluster/status" })).toBeNull();
  });
});


describe("Operations Brief last-known state", () => {
  it("omits the module when the first request fails", () => {
    expect(applyOperationsFailure(initialOperationsState)).toEqual({ brief: null, stale: false });
  });

  it("retains a successful Brief and marks it locally stale after failure", () => {
    const loaded = applyOperationsSuccess(initialOperationsState, brief);
    const failed = applyOperationsFailure(loaded);

    expect(failed.brief).toBe(brief);
    expect(failed.stale).toBe(true);
  });
});
