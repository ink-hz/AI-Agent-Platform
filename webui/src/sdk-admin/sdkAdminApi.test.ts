import { afterEach, describe, expect, it, vi } from 'vitest';
import { sdkAdminClient } from './sdkAdminApi';
const summary = { id: 'KB-NAV-004', title: '阅读协议', kind: 'knowledge', group: '00-navigation', level: 'L0', baseline_id: 'SDK-LOCAL', content_state: 'accepted_static', runtime_validation: 'not_run', source_path: 'docs/read.md', source_sha256: 'a'.repeat(64), review_status: 'independently_reviewed' };
afterEach(() => vi.unstubAllGlobals());
describe('SDK admin HTTP boundary', () => {
    it('encodes search and forwards cancellation and same-origin identity', async () => {
        const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ release_id: 'r1', items: [summary], total: 31, offset: 30, limit: 30 })));
        vi.stubGlobal('fetch', fetcher);
        const signal = new AbortController().signal;
        await sdkAdminClient.documents({ q: '设备 & 帧', group: '01-system', offset: 30, limit: 30 }, signal);
        const [path, options] = fetcher.mock.calls[0];
        expect(new URL(path, 'https://local').searchParams.get('q')).toBe('设备 & 帧');
        expect(options).toMatchObject({ credentials: 'same-origin', cache: 'no-store', signal });
    });
    it('rejects a nonempty page extending beyond total but allows an empty out-of-range page', async () => {
        vi.stubGlobal('fetch', vi.fn()
            .mockResolvedValueOnce(new Response(JSON.stringify({ release_id: 'r1', items: [summary], total: 1, offset: 30, limit: 30 })))
            .mockResolvedValueOnce(new Response(JSON.stringify({ release_id: 'r1', items: [], total: 1, offset: 30, limit: 30 }))));
        await expect(sdkAdminClient.documents({ offset: 30 })).rejects.toThrow(/invalid/i);
        await expect(sdkAdminClient.documents({ offset: 30 })).resolves.toMatchObject({ items: [], total: 1, offset: 30 });
    });
    it('retains forbidden and unavailable status without fallback data', async () => {
        const fetcher = vi.fn().mockResolvedValueOnce(new Response('', { status: 403 })).mockResolvedValueOnce(new Response('', { status: 503 }));
        vi.stubGlobal('fetch', fetcher);
        await expect(sdkAdminClient.catalog()).rejects.toMatchObject({ status: 403 });
        await expect(sdkAdminClient.catalog()).rejects.toMatchObject({ status: 503 });
    });
    it('rejects malformed summaries and detail identity mismatches', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ release_id: 'r', items: [{ ...summary, id: '../bad' }], total: 1, offset: 0, limit: 30 }))).mockResolvedValueOnce(new Response(JSON.stringify({ ...summary, id: 'KB-OTHER', markdown: 'body', sha256: 'b'.repeat(64), conditions: {}, links: {} }))));
        await expect(sdkAdminClient.documents({})).rejects.toThrow(/invalid/i);
        await expect(sdkAdminClient.document('KB-NAV-004')).rejects.toThrow(/invalid/i);
    });
    it('rejects non-string and traversal IDs before network access', async () => {
        const fetcher = vi.fn();
        vi.stubGlobal('fetch', fetcher);
        for (const id of ['../bad', 'a/b', '', 4, null])
            await expect(sdkAdminClient.document(id as string)).rejects.toThrow();
        expect(fetcher).not.toHaveBeenCalled();
    });
    it('rejects invalid paging, query and linked document IDs', async () => {
        const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...summary, markdown: 'body', sha256: 'b'.repeat(64), conditions: {}, links: { 'x.md': { document_id: '../bad', fragment: '' } } })));
        vi.stubGlobal('fetch', fetcher);
        await expect(sdkAdminClient.documents({ limit: 101 })).rejects.toThrow();
        await expect(sdkAdminClient.documents({ q: 'x'.repeat(201) })).rejects.toThrow();
        expect(fetcher).not.toHaveBeenCalled();
        await expect(sdkAdminClient.document('KB-NAV-004')).rejects.toThrow(/invalid/i);
    });
});
