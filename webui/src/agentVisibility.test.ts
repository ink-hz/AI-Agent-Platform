import { describe, expect, it } from "vitest";

import { agentsForSelector, businessAgents, partitionAgents } from "./agentVisibility";


const agents = [
  { id: "hr-bot", visibility: "business" as const },
  { id: "test-bot", visibility: "system" as const },
];


describe("Agent visibility", () => {
  it("partitions Business and System Agents without losing explicit diagnostics", () => {
    expect(businessAgents(agents).map((agent) => agent.id)).toEqual(["hr-bot"]);
    expect(partitionAgents(agents).system.map((agent) => agent.id)).toEqual(["test-bot"]);
    expect(agentsForSelector(agents, "").map((agent) => agent.id)).toEqual(["hr-bot"]);
    expect(agentsForSelector(agents, "test-bot").map((agent) => agent.id)).toEqual(["hr-bot", "test-bot"]);
  });
});
