/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { currentLocationPath, navigate, parseRoute, routePath, routeSection } from "./router";


afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
});


describe("Platform router", () => {
  it("parses use, account and unknown routes", () => {
    expect(parseRoute("/")).toEqual({ name: "brain" });
    expect(parseRoute("/agents")).toEqual({ name: "agents" });
    expect(parseRoute("/agents/ai-fae-agent")).toEqual({ name: "agent", agentId: "ai-fae-agent" });
    expect(parseRoute("/agents/voc/workspace")).toEqual({ name: "voc-workspace" });
    expect(parseRoute("/missions/one")).toEqual({ name: "mission", missionId: "one" });
    expect(parseRoute("/unknown")).toEqual({ name: "not-found" });
    expect(parseRoute("/login")).toEqual({ name: "login" });
    expect(parseRoute("/account")).toEqual({ name: "account" });
  });

  it("parses and serializes AI notes routes", () => {
    expect(parseRoute("/ai-notes")).toEqual({ name: "ai-notes" });
    const route = parseRoute("/ai-notes/agent-architecture/system-handbook");
    expect(route).toEqual({
      name: "ai-note",
      categorySlug: "agent-architecture",
      articleSlug: "system-handbook",
    });
    expect(routePath(route)).toBe("/ai-notes/agent-architecture/system-handbook");
    expect(routeSection(route)).toBe("ai-notes");
  });

  it("rejects malformed AI notes paths", () => {
    expect(parseRoute("/ai-notes/a/b/c")).toEqual({ name: "not-found" });
    expect(parseRoute("/ai-notes/../handbook")).toEqual({ name: "not-found" });
    expect(parseRoute("/ai-notes/UPPER/handbook")).toEqual({ name: "not-found" });
  });

  it("parses and navigates inside the isolated preview prefix", () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/account");
    expect(parseRoute(window.location.pathname)).toEqual({ name: "account" });
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);

    navigate("/agents");

    expect(window.location.pathname).toBe("/_preview/dingtalk-r1/agents");
  });

  it("creates encoded canonical detail paths", () => {
    expect(routePath({ name: "mission", missionId: "a/b" })).toBe("/missions/a%2Fb");
    expect(routePath({ name: "agent", agentId: "ai-fae-agent" })).toBe("/agents/ai-fae-agent");
    expect(routePath({ name: "admin-agent-runtime", agentId: "fae/a" })).toBe("/admin/agents/fae%2Fa/runtime");
    expect(routePath({ name: "admin-review" })).toBe("/admin/review");
    expect(routePath({ name: "admin-voc" })).toBe("/admin/voc");
    expect(routePath({ name: "account" })).toBe("/account");
    expect(routePath({ name: "voc-workspace" })).toBe("/agents/voc/workspace");
  });

  it("keeps detail pages in their parent navigation section", () => {
    expect(routeSection({ name: "agent", agentId: "ai-fae-agent" })).toBe("agents");
    expect(routeSection({ name: "mission", missionId: "one" })).toBe("missions");
    expect(routeSection({ name: "voc-workspace" })).toBe("agents");
    expect(routeSection({ name: "admin-session", sessionKey: "fae:abc" })).toBe("admin");
    expect(routeSection({ name: "admin-voc" })).toBe("admin");
  });

  it("treats search changes as navigation", () => {
    window.history.replaceState({}, "", "/admin/sessions?agent_id=one");
    navigate("/admin/sessions?agent_id=two", { replace: true });
    expect(currentLocationPath()).toBe("/admin/sessions?agent_id=two");
  });

  it("preserves caller state in a new history entry", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    navigate("/missions/one", { state: { returnTo: "/missions" } });
    expect(window.history.state).toEqual({ returnTo: "/missions" });
  });

  it("does not assign a legacy redirect to primary navigation", () => {
    expect(routeSection({ name: "legacy-redirect", to: "/admin/operations" })).toBeNull();
  });
});
