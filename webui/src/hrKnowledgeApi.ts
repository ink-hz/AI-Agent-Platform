import { platformPath } from "./auth";

export interface HrKnowledgeResource {
  id: string;
  title: string;
  revision: number;
  domains: string[];
  knowledgeForms: string[];
  path: string;
  sha256: string;
}

export interface HrKnowledgeIndex {
  sourceCommit: string;
  index: string;
  resources: HrKnowledgeResource[];
}

export interface HrKnowledgeArticle extends HrKnowledgeResource {
  sourceCommit: string;
  markdown: string;
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("HR knowledge response invalid");
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string" || !value) throw new Error("HR knowledge response invalid");
  return value;
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item)) throw new Error("HR knowledge response invalid");
  return [...value];
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) <= 0) throw new Error("HR knowledge response invalid");
  return Number(value);
}

function resource(value: unknown): HrKnowledgeResource {
  const item = object(value);
  const path = text(item.path);
  const sha256 = text(item.sha256);
  if (path.startsWith("/") || path.includes("..") || !/^[0-9a-f]{64}$/.test(sha256)) throw new Error("HR knowledge response invalid");
  return { id: text(item.id), title: text(item.title), revision: positiveInteger(item.revision),
    domains: strings(item.domains), knowledgeForms: strings(item.knowledge_forms), path, sha256 };
}

async function get(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), { credentials: "include", signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HR knowledge API ${response.status}`);
  return response.json();
}

export async function fetchHrKnowledgeIndex(signal?: AbortSignal): Promise<HrKnowledgeIndex> {
  const value = object(await get("/api/hr/knowledge", signal));
  if (!Array.isArray(value.resources)) throw new Error("HR knowledge response invalid");
  return { sourceCommit: text(value.source_commit), index: text(value.index), resources: value.resources.map(resource) };
}

export async function fetchHrKnowledgeArticle(sourceCommit: string, resourceId: string, signal?: AbortSignal): Promise<HrKnowledgeArticle> {
  const value = object(await get(`/api/hr/knowledge/${encodeURIComponent(sourceCommit)}/${encodeURIComponent(resourceId)}`, signal));
  return { ...resource(value), sourceCommit: text(value.source_commit), markdown: text(value.markdown) };
}
