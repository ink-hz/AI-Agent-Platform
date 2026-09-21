/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { OrganizationLayout } from './OrganizationLayout';
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const tree = { generation_id: id(9), root_id: id(1), completed_at: '2026-09-21T01:00:00Z', freshness: 'fresh', scope: 'visible_directory', departments: [
  { id: id(1), parent_id: null, name: 'Organization' }, { id: id(2), parent_id: id(1), name: '示例甲部' },
  { id: id(3), parent_id: id(1), name: '示例乙部' }, { id: id(4), parent_id: id(2), name: '示例小组' },
] };
const detail = (department = id(2), name = '虚构成员甲', next: string | null = null) => ({ generation_id: id(9), department_id: department, direct_count: 1, total_count: 2, status_counts: { active: 1, inactive: 1, disabled: 0 }, position_available: false, members: [{ id: id(name.endsWith('乙') ? 7 : 6), name, status: 'active', departments: [{ id: id(2), name: '示例甲部' }] }], next_cursor: next });
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>; let fetchMock: ReturnType<typeof vi.fn>; const denied = vi.fn();
const render = async (active = true) => { await act(async () => root.render(<OrganizationLayout active={active} onAuthorizationFailure={denied} />)); };
const click = async (label: string) => { const button = [...container.querySelectorAll('button')].find(b => b.textContent === label || b.getAttribute('aria-label') === label); expect(button).toBeTruthy(); await act(async () => button!.click()); };
beforeEach(() => { (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true; container = document.createElement('div'); document.body.append(container); root = createRoot(container); fetchMock = vi.fn().mockResolvedValue(response(tree)); vi.stubGlobal('fetch', fetchMock); denied.mockReset(); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
it('loads only when active, shows every first level and separates expansion from lazy details', async () => {
 await render(false); expect(fetchMock).not.toHaveBeenCalled(); await render();
 expect(container.textContent).toContain('公司组织'); expect(container.textContent).toContain('示例乙部'); expect(container.textContent).not.toMatch(/示例小组|虚构成员|人数|2026/);
 await click('展开示例甲部'); expect(container.textContent).toContain('示例小组'); expect(fetchMock).toHaveBeenCalledTimes(1);
 fetchMock.mockResolvedValueOnce(response(detail())); await click('示例甲部');
 expect(container.querySelector('[aria-label="部门详情"]')?.textContent).toContain('虚构成员甲'); expect(container.querySelector('[aria-label="组织层级"]')?.textContent).not.toMatch(/虚构成员|人数|2026/);
 expect(container.textContent).toContain('职位尚未同步'); expect(container.textContent).toContain('公司组织 / 示例甲部');
});
it('appends cursor pages and provides native keyboard buttons in nested lists', async () => {
 await render(); fetchMock.mockResolvedValueOnce(response(detail(id(2), '虚构成员甲', id(6)))); await click('示例甲部');
 fetchMock.mockResolvedValueOnce(response(detail(id(2), '虚构成员乙'))); await click('加载更多');
 expect(String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1]?.[0])).toContain(`cursor=${id(6)}`); expect(container.textContent).toContain('虚构成员甲'); expect(container.textContent).toContain('虚构成员乙');
 expect(container.querySelectorAll('[aria-label="组织层级"] ul ul').length).toBeGreaterThan(0); expect(container.querySelector('button[aria-expanded]')?.getAttribute('type')).toBe('button');
});
it('ignores old node response and clears/aborts when inactive', async () => {
 await render(); let resolve!: (r: Response) => void; fetchMock.mockImplementationOnce(() => new Promise(r => { resolve = r; })); await click('示例甲部');
 const signal = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]?.[1].signal as AbortSignal;
 fetchMock.mockResolvedValueOnce(response(detail(id(3), '虚构成员乙'))); await click('示例乙部'); expect(signal.aborted).toBe(true);
 await act(async () => resolve(response(detail()))); expect(container.textContent).not.toContain('虚构成员甲');
 await render(false); expect(container.textContent).not.toContain('虚构成员乙'); expect(container.querySelector('button')?.disabled).toBe(true); await render(); expect(container.textContent).not.toContain('虚构成员乙');
});
it('clears detail and refreshes structure after generation conflict', async () => {
 await render(); fetchMock.mockResolvedValueOnce(response({}, 409)).mockResolvedValueOnce(response({ ...tree, generation_id: id(10) })); await click('示例甲部');
 expect(fetchMock).toHaveBeenCalledTimes(3); expect(container.textContent).toContain('组织已更新'); expect(container.querySelector('[aria-label="部门详情"]')).toBeNull();
});
it.each([401, 403])('clears all private data and reports authorization status %s', async status => {
 await render(); fetchMock.mockResolvedValueOnce(response({}, status)); await click('示例甲部'); expect(denied).toHaveBeenCalledWith(expect.objectContaining({ status })); expect(container.textContent).not.toContain('示例甲部');
});
it('shows retry on failed detail without inventing zero counts', async () => {
 await render(); fetchMock.mockResolvedValueOnce(response({}, 503)); await click('示例甲部'); expect(container.textContent).toContain('重试详情'); expect(container.textContent).not.toContain('0 人');
});
it('shows stale snapshot only in details and treats department names as text', async () => {
 fetchMock.mockResolvedValueOnce(response({ ...tree, freshness: 'hard_stale', departments: tree.departments.map(d => d.id === id(2) ? { ...d, name: '<img src=x onerror=alert(1)>' } : d) }));
 await render(); expect(container.querySelector('img')).toBeNull(); expect(container.textContent).not.toContain('24');
 fetchMock.mockResolvedValueOnce(response(detail())); await click('<img src=x onerror=alert(1)>'); expect(container.textContent).toContain('超过 24 小时未更新'); expect(container.querySelector('img')).toBeNull();
});
it('can retry unavailable structure and aborts a pending detail on deactivation', async () => {
 fetchMock.mockResolvedValueOnce(response({}, 503)); await render(); expect(container.textContent).toContain('重试组织'); await click('重试组织');
 let resolve!: (r: Response) => void; fetchMock.mockImplementationOnce(() => new Promise(r => { resolve = r; })); await click('示例甲部'); const signal = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1].signal as AbortSignal;
 await render(false); expect(signal.aborted).toBe(true); await act(async () => resolve(response(detail()))); expect(container.textContent).not.toContain('虚构成员甲');
});
it('moves keyboard focus into detail and Escape returns it to the department', async () => {
 await render(); fetchMock.mockResolvedValueOnce(response(detail())); await click('示例甲部');
 const panel = container.querySelector<HTMLElement>('[aria-label="部门详情"]')!; expect(document.activeElement).toBe(panel);
 await act(async () => panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
 expect(container.querySelector('[aria-label="部门详情"]')).toBeNull(); expect(document.activeElement?.textContent).toBe('示例甲部');
});
