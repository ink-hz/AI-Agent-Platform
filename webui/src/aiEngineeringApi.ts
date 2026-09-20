import { platformPath } from "./auth";


export const AI_ENGINEERING_DOCUMENT_SLUGS = [
  "overview", "reading", "domains", "finance", "products", "assets",
] as const;
export type AiEngineeringDocumentSlug = typeof AI_ENGINEERING_DOCUMENT_SLUGS[number];

export interface AiEngineeringAccess { allowed: boolean }
export interface AiEngineeringDocumentSummary { slug: AiEngineeringDocumentSlug; title: string }
export interface AiEngineeringIndex {
  title: string;
  version: string;
  updated_at: string;
  diagram: { svg_path: string; png_path: string; alt: string };
  documents: AiEngineeringDocumentSummary[];
}
export interface AiEngineeringDocument {
  slug: AiEngineeringDocumentSlug;
  title: string;
  markdown: string;
}

export class AiEngineeringApiError extends Error {
  constructor(public status: number) {
    super(`AI engineering API ${status}`);
  }
}

export interface AiEngineeringClient {
  fetchAccess(signal?: AbortSignal): Promise<AiEngineeringAccess>;
  fetchIndex(signal?: AbortSignal): Promise<AiEngineeringIndex>;
  fetchDocument(slug: AiEngineeringDocumentSlug, signal?: AbortSignal): Promise<AiEngineeringDocument>;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isAiEngineeringDocumentSlug(value: string): value is AiEngineeringDocumentSlug {
  return (AI_ENGINEERING_DOCUMENT_SLUGS as readonly string[]).includes(value);
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}

function parseAccess(value: unknown): AiEngineeringAccess {
  if (!isObject(value) || !exactKeys(value, ["allowed"]) || typeof value.allowed !== "boolean") {
    throw new Error("AI engineering access response invalid");
  }
  return { allowed: value.allowed };
}

function parseSummary(value: unknown): AiEngineeringDocumentSummary {
  if (!isObject(value) || !exactKeys(value, ["slug", "title"])
    || typeof value.slug !== "string" || !isAiEngineeringDocumentSlug(value.slug)
    || typeof value.title !== "string" || !value.title) {
    throw new Error("AI engineering index response invalid");
  }
  return { slug: value.slug, title: value.title };
}

function parseIndex(value: unknown): AiEngineeringIndex {
  if (!isObject(value) || !exactKeys(value, ["title", "version", "updated_at", "diagram", "documents"])
    || typeof value.title !== "string" || !value.title
    || typeof value.version !== "string" || !value.version
    || typeof value.updated_at !== "string" || !value.updated_at
    || !isObject(value.diagram) || !exactKeys(value.diagram, ["svg_path", "png_path", "alt"])
    || typeof value.diagram.svg_path !== "string" || value.diagram.svg_path !== "/api/v1/ai-engineering/assets/panorama.svg"
    || typeof value.diagram.png_path !== "string" || value.diagram.png_path !== "/api/v1/ai-engineering/assets/panorama.png"
    || typeof value.diagram.alt !== "string" || !value.diagram.alt
    || !Array.isArray(value.documents)) {
    throw new Error("AI engineering index response invalid");
  }
  const documents = value.documents.map(parseSummary);
  if (documents.length !== new Set(documents.map(({ slug }) => slug)).size) {
    throw new Error("AI engineering index response invalid");
  }
  return {
    title: value.title,
    version: value.version,
    updated_at: value.updated_at,
    diagram: {
      svg_path: value.diagram.svg_path,
      png_path: value.diagram.png_path,
      alt: value.diagram.alt,
    },
    documents,
  };
}

function parseDocument(value: unknown, expectedSlug: AiEngineeringDocumentSlug): AiEngineeringDocument {
  if (!isObject(value) || !exactKeys(value, ["slug", "title", "markdown"])
    || value.slug !== expectedSlug
    || typeof value.title !== "string" || !value.title
    || typeof value.markdown !== "string") {
    throw new Error("AI engineering document response invalid");
  }
  return { slug: expectedSlug, title: value.title, markdown: value.markdown };
}

async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), {
    cache: "no-store",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new AiEngineeringApiError(response.status);
  return response.json();
}

export const aiEngineeringClient: AiEngineeringClient = {
  async fetchAccess(signal) {
    return parseAccess(await getJson("/api/v1/ai-engineering/access", signal));
  },
  async fetchIndex(signal) {
    return parseIndex(await getJson("/api/v1/ai-engineering", signal));
  },
  async fetchDocument(slug, signal) {
    if (!isAiEngineeringDocumentSlug(slug)) throw new Error("unsupported AI engineering document");
    return parseDocument(await getJson(`/api/v1/ai-engineering/documents/${slug}`, signal), slug);
  },
};
