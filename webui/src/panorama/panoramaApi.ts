import { AiEngineeringApiError, isAiEngineeringDocumentSlug } from "../aiEngineeringApi";
import { platformPath } from "../auth";
import type {
  PanoramaActionId, PanoramaData, PanoramaEdge, PanoramaEditorState, PanoramaGroup,
  PanoramaGroupRole, PanoramaLayer, PanoramaLayerKind, PanoramaNode, PanoramaSource,
} from "../panoramaTypes";

const ACTIONS = new Set<PanoramaActionId>([
  "brain", "agents", "missions", "sessions", "operations", "review", "activity", "identity", "governance",
  "access", "account", "agent-admin", "notes", "hr", "office", "voc", "fae",
]);
const LAYER_KINDS = new Set<PanoramaLayerKind>(["industry", "portfolio", "workflow", "support"]);
const GROUP_ROLES = new Set<PanoramaGroupRole>(["upstream", "company", "downstream", "products", "technology", "marketing", "delivery", "support"]);
const EDGE_KINDS = new Set(["supply", "supports", "feedback"]);
const ID = /^[a-z0-9][a-z0-9-]{0,63}$/;

function invalid(): never { throw new Error("AI engineering panorama response invalid"); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: readonly string[]) {
  if (Object.keys(value).length !== keys.length || keys.some((key) => !(key in value))) invalid();
}
function text(value: unknown, allowEmpty = false, maximum = Number.MAX_SAFE_INTEGER): string {
  if (typeof value !== "string" || value.length > maximum || [...value].some((character) => character.charCodeAt(0) < 32) || (!allowEmpty && !value.trim())) invalid();
  return value;
}
function id(value: unknown): string { const result = text(value); if (!ID.test(result)) invalid(); return result; }
function texts(value: unknown, allowEmpty = false): string[] {
  if (!Array.isArray(value)) invalid();
  const result = value.map((item) => text(item, allowEmpty));
  if (new Set(result).size !== result.length) invalid();
  return result;
}
function ids(value: unknown): string[] { const result = texts(value); if (result.some((item) => !ID.test(item))) invalid(); return result; }
function actions(value: unknown): PanoramaActionId[] {
  const result = texts(value);
  if (result.some((action) => !ACTIONS.has(action as PanoramaActionId))) invalid();
  return result as PanoramaActionId[];
}
function node(value: unknown): PanoramaNode {
  const item = object(value); exact(item, ["id", "title", "subtitle", "detail", "actions", "source_ids"]);
  if (!Array.isArray(item.detail) || item.detail.length > 12 || item.detail.some((entry) => typeof entry !== "string" || !entry.trim() || entry.length > 240)) invalid();
  const sourceIds = ids(item.source_ids); if (sourceIds.length > 20) invalid();
  return { id: id(item.id), title: text(item.title, false, 80), subtitle: text(item.subtitle, true, 160), detail: item.detail.map((entry) => text(entry, false, 240)), actions: actions(item.actions), source_ids: sourceIds };
}
function group(value: unknown): PanoramaGroup {
  const item = object(value); exact(item, ["id", "title", "role", "columns", "node_ids"]);
  const role = text(item.role); const columns = item.columns;
  if (!GROUP_ROLES.has(role as PanoramaGroupRole) || !Number.isSafeInteger(columns) || (columns as number) < 1 || (columns as number) > 8) invalid();
  const nodeIds = ids(item.node_ids); if (nodeIds.length > 40) invalid();
  return { id: id(item.id), title: text(item.title, false, 80), role: role as PanoramaGroupRole, columns: columns as number, node_ids: nodeIds };
}
function layer(value: unknown): PanoramaLayer {
  const item = object(value); exact(item, ["id", "title", "kind", "groups"]);
  const kind = text(item.kind); if (!LAYER_KINDS.has(kind as PanoramaLayerKind) || !Array.isArray(item.groups)) invalid();
  if (!item.groups.length || item.groups.length > 12) invalid();
  return { id: id(item.id), title: text(item.title, false, 80), kind: kind as PanoramaLayerKind, groups: item.groups.map(group) };
}
function edge(value: unknown): PanoramaEdge {
  const item = object(value); exact(item, ["id", "from", "to", "kind", "label"]);
  const kind = text(item.kind); if (!EDGE_KINDS.has(kind) || item.from === item.to) invalid();
  return { id: id(item.id), from: id(item.from), to: id(item.to), kind: kind as PanoramaEdge["kind"], label: text(item.label, true, 100) };
}
function source(value: unknown): PanoramaSource {
  const item = object(value); exact(item, ["id", "label", "document"]);
  const document = text(item.document); if (!isAiEngineeringDocumentSlug(document)) invalid();
  return { id: id(item.id), label: text(item.label, false, 160), document };
}
function unique(values: string[]): boolean { return values.length === new Set(values).size; }

export function parsePanorama(value: unknown): PanoramaData {
  const root = object(value); exact(root, ["version", "updated_at", "title", "layers", "nodes", "edges", "sources"]);
  if (!Array.isArray(root.layers) || !Array.isArray(root.nodes) || !Array.isArray(root.edges) || !Array.isArray(root.sources)) invalid();
  if (root.layers.length !== 4 || root.nodes.length > 80 || root.edges.length > 240 || root.sources.length > 80) invalid();
  const layers = root.layers.map(layer); const nodes = root.nodes.map(node); const edges = root.edges.map(edge); const sources = root.sources.map(source);
  const layerIds = layers.map((item) => item.id); const groupIds = layers.flatMap((item) => item.groups.map((entry) => entry.id));
  const nodeIds = nodes.map((item) => item.id); const edgeIds = edges.map((item) => item.id); const sourceIds = sources.map((item) => item.id);
  const placed = layers.flatMap((item) => item.groups.flatMap((entry) => entry.node_ids));
  if (!unique(layerIds) || !unique(groupIds) || !unique(nodeIds) || !unique(edgeIds) || !unique(sourceIds)
    || layers.length !== 4 || !unique(layers.map((item) => item.kind)) || LAYER_KINDS.size !== layers.length
    || !unique(placed) || placed.length !== nodeIds.length || placed.some((item) => !nodeIds.includes(item)) || nodeIds.some((item) => !placed.includes(item))
    || edges.some((item) => !nodeIds.includes(item.from) || !nodeIds.includes(item.to))
    || nodes.some((item) => item.source_ids.some((sourceId) => !sourceIds.includes(sourceId)))) invalid();
  return { version: text(root.version, false, 96), updated_at: text(root.updated_at, false, 64), title: text(root.title, false, 120), layers, nodes, edges, sources };
}

function nullablePanorama(value: unknown): PanoramaData | null { return value === null ? null : parsePanorama(value); }
export function parsePanoramaEditorState(value: unknown): PanoramaEditorState {
  const root = object(value); exact(root, ["revision", "published", "draft", "previous"]);
  if (!Number.isSafeInteger(root.revision) || (root.revision as number) < 0) invalid();
  return { revision: root.revision as number, published: parsePanorama(root.published), draft: nullablePanorama(root.draft), previous: nullablePanorama(root.previous) };
}

async function read(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), { cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" }, signal });
  if (!response.ok) throw new AiEngineeringApiError(response.status);
  return response.json();
}
async function write(path: string, method: "PUT" | "POST" | "DELETE", csrfToken: string, body: unknown, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), {
    method, cache: "no-store", credentials: "same-origin", signal,
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new AiEngineeringApiError(response.status);
  return response.json();
}

export interface PanoramaClient {
  fetchPanorama(signal?: AbortSignal): Promise<PanoramaData>;
  fetchEditorState(signal?: AbortSignal): Promise<PanoramaEditorState>;
  saveDraft(csrfToken: string, expectedRevision: number, data: PanoramaData, signal?: AbortSignal): Promise<PanoramaEditorState>;
  discardDraft(csrfToken: string, expectedRevision: number, signal?: AbortSignal): Promise<PanoramaEditorState>;
  publish(csrfToken: string, expectedRevision: number, signal?: AbortSignal): Promise<PanoramaEditorState>;
  restore(csrfToken: string, expectedRevision: number, signal?: AbortSignal): Promise<PanoramaEditorState>;
}

export async function fetchPanorama(signal?: AbortSignal): Promise<PanoramaData> { return parsePanorama(await read("/api/v1/ai-engineering/panorama", signal)); }
export const panoramaClient: PanoramaClient = {
  fetchPanorama,
  async fetchEditorState(signal) { return parsePanoramaEditorState(await read("/api/v1/ai-engineering/panorama/draft", signal)); },
  async saveDraft(csrfToken, expectedRevision, data, signal) { return parsePanoramaEditorState(await write("/api/v1/ai-engineering/panorama/draft", "PUT", csrfToken, { expected_revision: expectedRevision, data }, signal)); },
  async discardDraft(csrfToken, expectedRevision, signal) { return parsePanoramaEditorState(await write("/api/v1/ai-engineering/panorama/draft", "DELETE", csrfToken, { expected_revision: expectedRevision }, signal)); },
  async publish(csrfToken, expectedRevision, signal) { return parsePanoramaEditorState(await write("/api/v1/ai-engineering/panorama/publish", "POST", csrfToken, { expected_revision: expectedRevision }, signal)); },
  async restore(csrfToken, expectedRevision, signal) { return parsePanoramaEditorState(await write("/api/v1/ai-engineering/panorama/restore", "POST", csrfToken, { expected_revision: expectedRevision }, signal)); },
};
