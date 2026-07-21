import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";


describe("AgentDetailPage lifecycle diagnostics", () => {
  it("loads Fleet lifecycle data and keeps process runtime in the detail view", () => {
    const source = readFileSync(new URL("./AgentDetailPage.tsx", import.meta.url), "utf8");

    for (const expected of [
      "fetchFleetOverview",
      "Live Since",
      "Last Updated",
      "Current Runtime",
      "formatLifecycleBasis",
      "formatRuntimeDuration",
    ]) {
      expect(source).toContain(expected);
    }
  });
});
