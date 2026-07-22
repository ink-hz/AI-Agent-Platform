import { describe, expect, it } from "vitest";

import { parseRoute, routePath, routeSection } from "./router";


describe("Platform router", () => {
  it("parses every product route and decodes stable keys", () => {
    expect(parseRoute("/")).toEqual({ name: "overview" });
    expect(parseRoute("/agents")).toEqual({ name: "agents" });
    expect(parseRoute("/agents/ai-fae-agent")).toEqual({ name: "agent", agentId: "ai-fae-agent" });
    expect(parseRoute("/sessions")).toEqual({ name: "sessions" });
    expect(parseRoute("/sessions/fae%3Aabc")).toEqual({ name: "session", sessionKey: "fae:abc" });
    expect(parseRoute("/flywheel")).toEqual({ name: "flywheel" });
    expect(parseRoute("/unknown")).toEqual({ name: "not-found" });
  });

  it("creates encoded detail paths", () => {
    expect(routePath({ name: "session", sessionKey: "fae:a/b" })).toBe("/sessions/fae%3Aa%2Fb");
    expect(routePath({ name: "agent", agentId: "ai-fae-agent" })).toBe("/agents/ai-fae-agent");
  });

  it("keeps detail pages in their parent navigation section", () => {
    expect(routeSection({ name: "agent", agentId: "ai-fae-agent" })).toBe("agents");
    expect(routeSection({ name: "session", sessionKey: "fae:abc" })).toBe("sessions");
  });

  it("routes Activity without assigning it a primary navigation section", () => {
    expect(parseRoute("/activity")).toEqual({ name: "activity" });
    expect(routePath({ name: "activity" })).toBe("/activity");
    expect(routeSection({ name: "activity" })).toBeNull();
  });
});
