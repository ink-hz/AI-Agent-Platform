import type { AgentVisibility } from "./types";


export function businessAgents<T extends { visibility: AgentVisibility }>(agents: T[]): T[] {
  return agents.filter((agent) => agent.visibility === "business");
}


export function partitionAgents<T extends { visibility: AgentVisibility }>(agents: T[]) {
  return {
    business: businessAgents(agents),
    system: agents.filter((agent) => agent.visibility === "system"),
  };
}


export function agentsForSelector<T extends { id: string; visibility: AgentVisibility }>(
  agents: T[],
  selectedId: string,
): T[] {
  return agents.filter(
    (agent) => agent.visibility === "business" || agent.id === selectedId,
  );
}
