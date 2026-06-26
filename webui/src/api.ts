import type { Agent, Health } from "./types";

export async function fetchAgents(): Promise<Agent[]> {
  const response = await fetch("/api/agents");
  if (!response.ok) throw new Error(`agents ${response.status}`);
  return response.json();
}

export async function fetchHealth(): Promise<Health[]> {
  const response = await fetch("/api/agents/health");
  if (!response.ok) throw new Error(`health ${response.status}`);
  return response.json();
}
