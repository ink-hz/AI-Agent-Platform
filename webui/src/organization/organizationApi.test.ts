import { afterEach, expect, it, vi } from 'vitest';
import { fetchOrganization, fetchDepartment, parseOrganization, parseDepartment } from './organizationApi';
const id = '00000000-0000-4000-8000-000000000001';
const tree = { generation_id: id, completed_at: '2026-09-21T01:00:00Z', freshness: 'warning', scope: 'visible_directory', root_id: id, departments: [{ id, name: 'Organization', parent_id: null }] };
afterEach(() => vi.unstubAllGlobals());
it('validates tree topology, timestamp, freshness and private response shape', () => {
 expect(parseOrganization(tree).freshness).toBe('warning');
 for (const patch of [{ completed_at: 'bad' }, { freshness: 'other' }, { departments: [] }, { departments: [{ id, parent_id: id, name: 'cycle' }] }, { members: [] }]) expect(() => parseOrganization({ ...tree, ...patch })).toThrow();
});
it('rejects malformed counts and member status rather than showing false data', () => {
 const value = { generation_id: id, department_id: id, direct_count: 0, total_count: 0, status_counts: { active: 0, inactive: 0, disabled: 0 }, position_available: false, members: [], next_cursor: null };
 expect(parseDepartment(value).members).toEqual([]); expect(() => parseDepartment({ ...value, total_count: -1 })).toThrow(); expect(() => parseDepartment({ ...value, members: [{ id, name: '虚构', status: 'unknown', departments: [] }] })).toThrow();
});
it('uses no-store credentials and binds detail to generation/cursor', async () => {
 const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(tree))); vi.stubGlobal('fetch', fetch); await fetchOrganization(); expect(fetch.mock.calls[0][1]).toMatchObject({ cache: 'no-store', credentials: 'same-origin' });
 fetch.mockResolvedValue(new Response('{}', { status: 409 })); await expect(fetchDepartment(id, id, id)).rejects.toMatchObject({ status: 409 }); expect(fetch.mock.calls[1][0]).toContain(`generation_id=${id}&limit=50&cursor=${id}`);
});
