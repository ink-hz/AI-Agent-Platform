/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { currentLocationPath, navigate, parseRoute, routePath, routeSection } from "./router";


afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
});


describe("Platform router", () => {
  it("parses every product route and decodes stable keys", () => {
    expect(parseRoute("/")).toEqual({ name: "overview" });
    expect(parseRoute("/agents")).toEqual({ name: "agents" });
    expect(parseRoute("/agents/ai-fae-agent")).toEqual({ name: "agent", agentId: "ai-fae-agent" });
    expect(parseRoute("/agents/ai-fae-agent/runtime")).toEqual({ name: "agent-runtime", agentId: "ai-fae-agent" });
    expect(parseRoute("/sessions")).toEqual({ name: "sessions" });
    expect(parseRoute("/sessions/fae%3Aabc")).toEqual({ name: "session", sessionKey: "fae:abc" });
    expect(parseRoute("/flywheel")).toEqual({ name: "flywheel" });
    expect(parseRoute("/unknown")).toEqual({ name: "not-found" });
  });

  it("creates encoded detail paths", () => {
    expect(routePath({ name: "session", sessionKey: "fae:a/b" })).toBe("/sessions/fae%3Aa%2Fb");
    expect(routePath({ name: "agent", agentId: "ai-fae-agent" })).toBe("/agents/ai-fae-agent");
    expect(routePath({ name: "agent-runtime", agentId: "fae/a" })).toBe("/agents/fae%2Fa/runtime");
  });

  it("keeps detail pages in their parent navigation section", () => {
    expect(routeSection({ name: "agent", agentId: "ai-fae-agent" })).toBe("agents");
    expect(routeSection({ name: "agent-runtime", agentId: "ai-fae-agent" })).toBe("agents");
    expect(routeSection({ name: "session", sessionKey: "fae:abc" })).toBe("sessions");
  });

  it("routes Activity without assigning it a primary navigation section", () => {
    expect(parseRoute("/activity")).toEqual({ name: "activity" });
    expect(routePath({ name: "activity" })).toBe("/activity");
    expect(routeSection({ name: "activity" })).toBeNull();
  });

  it("treats search changes as navigation", () => {
    window.history.replaceState({}, "", "/sessions?agent_id=one");

    navigate("/sessions?agent_id=two", { replace: true });

    expect(currentLocationPath()).toBe("/sessions?agent_id=two");
  });

  it("preserves caller state in a new history entry", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);

    navigate("/sessions/fae%3Aone", { state: { returnTo: "/sessions" } });

    expect(window.history.state).toEqual({ returnTo: "/sessions" });
  });

  it("does not assign the legacy Flywheel route to primary navigation", () => {
    expect(routeSection({ name: "flywheel" })).toBeNull();
  });
});
