import { AiEngineeringApiError, isAiEngineeringDocumentSlug } from "../aiEngineeringApi";
import { platformPath } from "../auth";
import type { PanoramaActionId, PanoramaData, PanoramaDomain, PanoramaSource } from "../panoramaTypes";


const ACTIONS = new Set<PanoramaActionId>([
  "brain", "agents", "missions", "sessions", "operations", "review", "activity", "identity", "governance",
  "access", "account", "agent-admin", "notes", "hr", "office", "voc", "fae",
]);

function invalid(): never { throw new Error("AI engineering panorama response invalid"); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: readonly string[]) {
  if (Object.keys(value).length !== keys.length || keys.some((key) => !(key in value))) invalid();
}
function text(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) invalid();
  return value;
}
function texts(value: unknown): string[] {
  if (!Array.isArray(value)) invalid();
  const result = value.map(text);
  if (new Set(result).size !== result.length) invalid();
  return result;
}
function actions(value: unknown): PanoramaActionId[] {
  const result = texts(value);
  if (result.some((action) => !ACTIONS.has(action as PanoramaActionId))) invalid();
  return result as PanoramaActionId[];
}
function domain(value: unknown): PanoramaDomain {
  const item = object(value);
  exact(item, ["id", "title", "subtitle", "items", "detail", "status", "source_ids", "related_ids", "actions"]);
  return {
    id: text(item.id), title: text(item.title), subtitle: text(item.subtitle), items: texts(item.items),
    detail: texts(item.detail), status: text(item.status), source_ids: texts(item.source_ids),
    related_ids: texts(item.related_ids), actions: actions(item.actions),
  };
}
function source(value: unknown): PanoramaSource {
  const item = object(value); exact(item, ["id", "label", "document"]);
  const document = text(item.document);
  if (!isAiEngineeringDocumentSlug(document)) invalid();
  return { id: text(item.id), label: text(item.label), document };
}

export function parsePanorama(value: unknown): PanoramaData {
  const root = object(value);
  exact(root, ["version", "updated_at", "title", "context", "revenue", "domains", "support", "shared", "asks", "sources"]);
  const context = object(root.context);
  exact(context, ["period", "metrics", "observation", "judgment", "source_ids"]);
  if (!Array.isArray(context.metrics)) invalid();
  const metrics = context.metrics.map((value) => {
    const metric = object(value); exact(metric, ["label", "value"]);
    return { label: text(metric.label), value: text(metric.value) };
  });
  const revenue = object(root.revenue);
  exact(revenue, ["period", "denominator_cents", "segments", "note", "source_ids"]);
  if (!Number.isSafeInteger(revenue.denominator_cents) || (revenue.denominator_cents as number) <= 0 || !Array.isArray(revenue.segments)) invalid();
  const segments = revenue.segments.map((value) => {
    const segment = object(value); exact(segment, ["id", "label", "amount_cents"]);
    if (!Number.isSafeInteger(segment.amount_cents) || (segment.amount_cents as number) < 0) invalid();
    return { id: text(segment.id), label: text(segment.label), amount_cents: segment.amount_cents as number };
  });
  if (segments.reduce((sum, item) => sum + item.amount_cents, 0) > (revenue.denominator_cents as number)) invalid();
  if (!Array.isArray(root.domains) || !Array.isArray(root.support) || !Array.isArray(root.asks) || !Array.isArray(root.sources)) invalid();
  const domains = root.domains.map(domain); const support = root.support.map(domain); const allDomains = [...domains, ...support];
  const shared = object(root.shared); exact(shared, ["title", "actions", "status"]);
  const asks = root.asks.map((value) => { const ask = object(value); exact(ask, ["owner", "request"]); return { owner: text(ask.owner), request: text(ask.request) }; });
  const sources = root.sources.map(source);
  const domainIds = allDomains.map(({ id }) => id); const sourceIds = sources.map(({ id }) => id);
  if (new Set(domainIds).size !== domainIds.length || new Set(sourceIds).size !== sourceIds.length
    || allDomains.some((item) => item.related_ids.some((id) => !domainIds.includes(id)))
    || [...texts(context.source_ids), ...texts(revenue.source_ids), ...allDomains.flatMap((item) => item.source_ids)].some((id) => !sourceIds.includes(id))) invalid();
  return {
    version: text(root.version), updated_at: text(root.updated_at), title: text(root.title),
    context: { period: text(context.period), metrics, observation: text(context.observation), judgment: text(context.judgment), source_ids: texts(context.source_ids) },
    revenue: { period: text(revenue.period), denominator_cents: revenue.denominator_cents as number, segments, note: text(revenue.note), source_ids: texts(revenue.source_ids) },
    domains, support,
    shared: { title: text(shared.title), actions: actions(shared.actions), status: text(shared.status) }, asks, sources,
  };
}

export interface PanoramaClient { fetchPanorama(signal?: AbortSignal): Promise<PanoramaData> }

export async function fetchPanorama(signal?: AbortSignal): Promise<PanoramaData> {
    const response = await fetch(platformPath("/api/v1/ai-engineering/panorama"), {
      cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" }, signal,
    });
    if (!response.ok) throw new AiEngineeringApiError(response.status);
    return parsePanorama(await response.json());
}

export const panoramaClient: PanoramaClient = { fetchPanorama };
