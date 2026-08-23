/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import { parseRoute, routePath, routeSection } from "./router";


describe("Agent Brain route boundary", () => {
  it("makes use routes the primary product surface", () => {
    expect(parseRoute("/")).toEqual({ name: "brain" });
    expect(parseRoute("/conversations")).toEqual({ name: "conversations" });
    expect(parseRoute("/conversations/8c13c965-1b60-472e-b275-199987d1d109")).toEqual({
      name: "conversation",
      conversationId: "8c13c965-1b60-472e-b275-199987d1d109",
    });
    expect(parseRoute("/missions")).toEqual({ name: "missions" });
    expect(parseRoute("/missions/8c13c965-1b60-472e-b275-199987d1d109")).toEqual({
      name: "mission",
      missionId: "8c13c965-1b60-472e-b275-199987d1d109",
    });
    expect(parseRoute("/agents")).toEqual({ name: "agents" });
    expect(parseRoute("/agents/hr-bot")).toEqual({ name: "agent", agentId: "hr-bot" });
    expect(routeSection({ name: "mission", missionId: "one" })).toBe("missions");
    expect(routeSection({ name: "conversation", conversationId: "one" })).toBe("conversations");
  });

  it("places every management page beneath the admin namespace", () => {
    expect(parseRoute("/admin")).toEqual({ name: "admin-overview" });
    expect(parseRoute("/admin/overview")).toEqual({ name: "admin-overview" });
    expect(parseRoute("/admin/agents")).toEqual({ name: "admin-agents" });
    expect(parseRoute("/admin/agents/hr-bot")).toEqual({ name: "admin-agent", agentId: "hr-bot" });
    expect(parseRoute("/admin/agents/hr-bot/runtime")).toEqual({ name: "admin-agent-runtime", agentId: "hr-bot" });
    expect(parseRoute("/admin/sessions")).toEqual({ name: "admin-sessions" });
    expect(parseRoute("/admin/sessions/fae%3Aone")).toEqual({ name: "admin-session", sessionKey: "fae:one" });
    expect(parseRoute("/admin/review")).toEqual({ name: "admin-review" });
    expect(parseRoute("/admin/activity")).toEqual({ name: "admin-activity" });
    expect(parseRoute("/admin/operations")).toEqual({ name: "admin-operations" });
    expect(parseRoute("/admin/identity")).toEqual({ name: "admin-identity" });
    expect(parseRoute("/admin/governance")).toEqual({ name: "admin-governance" });
  });

  it.each([
    ["/review", "/admin/review"],
    ["/activity", "/admin/activity"],
    ["/flywheel", "/admin/operations"],
    ["/identity", "/admin/identity"],
    ["/governance", "/admin/governance"],
    ["/sessions", "/admin/sessions"],
    ["/sessions/fae%3Aone", "/admin/sessions/fae%3Aone"],
    ["/agents/hr-bot/runtime", "/admin/agents/hr-bot/runtime"],
  ])("has an explicit permanent client redirect from %s", (legacy, target) => {
    expect(parseRoute(legacy)).toEqual({ name: "legacy-redirect", to: target });
  });

  it("generates canonical paths instead of legacy management URLs", () => {
    expect(routePath({ name: "brain" })).toBe("/");
    expect(routePath({ name: "conversations" })).toBe("/conversations");
    expect(routePath({ name: "conversation", conversationId: "a/b" })).toBe("/conversations/a%2Fb");
    expect(routePath({ name: "missions" })).toBe("/missions");
    expect(routePath({ name: "mission", missionId: "a/b" })).toBe("/missions/a%2Fb");
    expect(routePath({ name: "admin-review" })).toBe("/admin/review");
    expect(routePath({ name: "admin-agent-runtime", agentId: "fae/a" })).toBe("/admin/agents/fae%2Fa/runtime");
  });
});
