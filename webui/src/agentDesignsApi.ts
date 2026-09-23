import { platformPath } from "./auth";
export interface AgentDesign {
  slug: string; agent: string; title: string;
  source: { repository: string; path: string; commit: string; working_tree_modified: boolean; sha256: string; captured_at: string };
}
export interface AgentDesignDocument extends AgentDesign { markdown: string }
export class AgentDesignError extends Error {
  constructor(public status: number) { super("Agent design read failed"); }
}
async function read(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), { signal, credentials: "include", headers: { Accept: "application/json" } });
  if (!response.ok) throw new AgentDesignError(response.status);
  return response.json();
}
function design(value: unknown): value is AgentDesign {
  if (!value || typeof value !== "object") return false;
  const d = value as AgentDesign;
  return typeof d.slug === "string" && /^[a-z][a-z0-9-]{0,63}$/.test(d.slug) && typeof d.agent === "string" && typeof d.title === "string"
    && !!d.source && typeof d.source.repository === "string" && typeof d.source.path === "string"
    && typeof d.source.commit === "string" && /^[0-9a-f]{40}$/.test(d.source.commit)
    && typeof d.source.sha256 === "string" && /^[0-9a-f]{64}$/.test(d.source.sha256)
    && typeof d.source.working_tree_modified === "boolean" && typeof d.source.captured_at === "string";
}
export async function listAgentDesigns(signal: AbortSignal): Promise<AgentDesign[]> {
  const data = await read("/api/v1/manage/agent-designs", signal) as { documents?: unknown };
  if (!data || !Array.isArray(data.documents) || !data.documents.every(design)) throw new Error("Invalid design index");
  return data.documents;
}
export async function getAgentDesign(slug: string, signal: AbortSignal): Promise<AgentDesignDocument> {
  const data = await read(`/api/v1/manage/agent-designs/${encodeURIComponent(slug)}`, signal) as AgentDesignDocument;
  if (!design(data) || data.slug !== slug || typeof data.markdown !== "string") throw new Error("Invalid design document");
  return data;
}
