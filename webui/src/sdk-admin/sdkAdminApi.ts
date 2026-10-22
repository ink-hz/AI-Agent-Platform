import { platformPath } from '../auth';
export type DocumentKind = 'knowledge' | 'process' | 'analysis' | 'design' | 'acceptance' | 'execution';
export interface DocumentSummary {
    id: string;
    title: string;
    kind: DocumentKind;
    group: string;
    level: string | null;
    baseline_id: string | null;
    content_state: string;
    runtime_validation: string;
    source_path: string;
    source_sha256: string;
    review_status: string;
}
export interface DocumentDetail extends DocumentSummary {
    markdown: string;
    sha256: string;
    conditions: Record<string, unknown>;
    links: Record<string, {
        document_id: string;
        fragment: string;
    }>;
}
export interface SourceStatus {
    id: string;
    label: string;
    state: string;
    description: string;
    href?: string;
}
export interface Catalog {
    release_id: string;
    baseline_id: string;
    generated_at: string;
    manifest_sha256: string;
    counts: {
        knowledge_pages: number;
        knowledge_navigation: number;
        documents: number;
    };
    groups: {
        id: string;
        title: string;
        count: number;
    }[];
    sources: SourceStatus[];
    ai: {
        available: false;
        reason: string;
    };
    featured_ids: string[];
}
export interface DocumentList {
    release_id: string;
    items: DocumentSummary[];
    total: number;
    offset: number;
    limit: number;
}
export interface DocumentQuery {
    q?: string;
    kind?: DocumentKind;
    group?: string;
    offset?: number;
    limit?: number;
}
export interface SdkAdminClient {
    access(signal?: AbortSignal): Promise<{
        allowed: boolean;
    }>;
    catalog(signal?: AbortSignal): Promise<Catalog>;
    documents(query: DocumentQuery, signal?: AbortSignal): Promise<DocumentList>;
    document(id: string, signal?: AbortSignal): Promise<DocumentDetail>;
}
export class SdkAdminApiError extends Error {
    constructor(public status: number) { super(`SDK admin API ${status}`); }
}
export function isDocumentId(value: unknown): value is string { return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(value); }
const kinds: readonly string[] = ['knowledge', 'process', 'analysis', 'design', 'acceptance', 'execution'];
const obj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const integer = (v: unknown, min = 0): v is number => Number.isSafeInteger(v) && Number(v) >= min;
function invalid(): never { throw new Error('SDK admin response invalid'); }
function summary(v: unknown): DocumentSummary {
    if (!obj(v) || !isDocumentId(v.id) || !str(v.title) || !kinds.includes(v.kind as string) || !str(v.group) || !(v.level === null || str(v.level)) || !(v.baseline_id === null || str(v.baseline_id)) || !str(v.content_state) || !str(v.runtime_validation) || !str(v.source_path) || !hash(v.source_sha256) || !str(v.review_status))
        return invalid();
    return v as unknown as DocumentSummary;
}
function catalog(v: unknown): Catalog {
    if (!obj(v) || !str(v.release_id) || !str(v.baseline_id) || !str(v.generated_at) || Number.isNaN(Date.parse(v.generated_at)) || !hash(v.manifest_sha256) || !obj(v.counts) || !['knowledge_pages', 'knowledge_navigation', 'documents'].every(k => integer((v.counts as Record<string, unknown>)[k])) || !Array.isArray(v.groups) || !v.groups.every(g => obj(g) && str(g.id) && str(g.title) && integer(g.count)) || !Array.isArray(v.sources) || !v.sources.every(s => obj(s) && str(s.id) && str(s.label) && str(s.state) && typeof s.description === 'string' && (s.href === undefined || typeof s.href === 'string')) || !obj(v.ai) || v.ai.available !== false || !str(v.ai.reason) || !Array.isArray(v.featured_ids) || !v.featured_ids.every(isDocumentId))
        return invalid();
    if (new Set(v.groups.map(g => g.id)).size !== v.groups.length)
        return invalid();
    return v as unknown as Catalog;
}
async function get(path: string, signal?: AbortSignal): Promise<unknown> { const res = await fetch(platformPath(`/api/v1/sdk-admin${path}`), { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal }); if (!res.ok)
    throw new SdkAdminApiError(res.status); return res.json(); }
export const sdkAdminClient: SdkAdminClient = {
    async access(signal) { const v = await get('/access', signal); if (!obj(v) || typeof v.allowed !== 'boolean')
        return invalid(); return { allowed: v.allowed }; },
    async catalog(signal) { return catalog(await get('/catalog', signal)); },
    async documents(query, signal) {
        const { q, kind, group, offset = 0, limit = 30 } = query;
        if ((q !== undefined && (typeof q !== 'string' || q.length > 200)) || (kind !== undefined && !kinds.includes(kind)) || (group !== undefined && typeof group !== 'string') || !integer(offset) || !integer(limit, 1) || limit > 100)
            throw new Error('SDK admin query invalid');
        const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
        if (q)
            params.set('q', q);
        if (kind)
            params.set('kind', kind);
        if (group)
            params.set('group', group);
        const v = await get(`/documents?${params}`, signal);
        if (!obj(v) || !str(v.release_id) || !Array.isArray(v.items) || !integer(v.total) || !integer(v.offset) || !integer(v.limit, 1) || v.limit > 100)
            return invalid();
        const items = v.items.map(summary);
        if (items.length > v.limit || new Set(items.map(x => x.id)).size !== items.length || (items.length > 0 && v.offset + items.length > v.total) || v.offset !== offset || v.limit !== limit)
            return invalid();
        return { ...v, items } as unknown as DocumentList;
    },
    async document(id, signal) { if (!isDocumentId(id))
        throw new Error('SDK admin document ID invalid'); const v = await get(`/documents/${id}`, signal); const base = summary(v); if (!obj(v) || base.id !== id || typeof v.markdown !== 'string' || !hash(v.sha256) || !obj(v.conditions) || !obj(v.links) || !Object.values(v.links).every(l => obj(l) && isDocumentId(l.document_id) && typeof l.fragment === 'string' && !/[\u0000-\u001f]/.test(l.fragment)))
        return invalid(); return v as unknown as DocumentDetail; }
};
