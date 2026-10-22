// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Account } from '../auth';
import { SdkAdminPage } from './SdkAdminPage';
import { SdkDocumentReader } from './SdkDocumentReader';
import { SdkAdminApiError, type Catalog, type DocumentDetail, type DocumentList, type SdkAdminClient } from './sdkAdminApi';
vi.mock('../router', () => ({ navigate: vi.fn() }));
const account = { internal_user_id: 'reader', display_name: 'Reader' } as Account;
const catalog: Catalog = { release_id: 'r1', baseline_id: 'SDK-LOCAL', generated_at: '2026-10-22T10:00:00Z', manifest_sha256: 'a'.repeat(64), counts: { knowledge_pages: 865, knowledge_navigation: 19, documents: 893 }, groups: [{ id: '01-system', title: '系统架构', count: 4 }], sources: [], ai: { available: false, reason: 'SDK 工具尚未接入' }, featured_ids: [] };
const detail: DocumentDetail = { id: 'KB-NAV-004', title: '阅读协议', kind: 'knowledge', group: '01-system', level: 'L0', baseline_id: 'SDK-LOCAL', content_state: 'accepted_static', runtime_validation: 'not_run', source_path: 'docs/read.md', source_sha256: 'a'.repeat(64), review_status: 'independently_reviewed', markdown: '# 阅读协议\n\n正文内容', sha256: 'b'.repeat(64), conditions: { device: '设备 A' }, links: {} };
const list: DocumentList = { release_id: 'r1', items: [detail], total: 65, offset: 0, limit: 30 };
function makeClient(): SdkAdminClient { return { access: vi.fn().mockResolvedValue({ allowed: true }), catalog: vi.fn().mockResolvedValue(catalog), documents: vi.fn().mockResolvedValue(list), document: vi.fn().mockResolvedValue(detail) }; }
let box: HTMLDivElement, root: Root;
beforeEach(() => { (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true; box = document.createElement('div'); document.body.append(box); root = createRoot(box); });
afterEach(async () => { await act(async () => root.unmount()); box.remove(); vi.clearAllMocks(); });
async function render(section: 'overview' | 'products' | 'delivery' | 'diagnostics' | 'knowledge' = 'knowledge', client = makeClient(), id?: string) { await act(async () => root.render(<SdkAdminPage account={account} section={section} client={client} documentId={id}/>)); return client; }
function click(text: string) { const element = Array.from(box.querySelectorAll('button')).find(x => x.textContent === text); expect(element).toBeTruthy(); element!.click(); }
it('does not fetch or expose catalog content without SDK permission', async () => { const client = makeClient(); vi.mocked(client.access).mockResolvedValue({ allowed: false }); await render('overview', client); expect(box.textContent).toContain('无 SDK 阅读权限'); expect(client.catalog).not.toHaveBeenCalled(); expect(box.textContent).not.toContain('865'); });
it.each([403, 503])('shows status %s without sample content', async (status) => { const client = makeClient(); vi.mocked(client.catalog).mockRejectedValue(new SdkAdminApiError(status)); await render('overview', client); expect(box.querySelector('[role="alert"]')).toBeTruthy(); expect(box.textContent).not.toContain('阅读协议'); expect(box.textContent).not.toContain('865'); });
it('searches and groups on the server and resets pagination', async () => {
    const client = await render();
    await act(async () => click('下一页'));
    expect(client.documents).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 30 }), expect.any(AbortSignal));
    const input = box.querySelector('input[type="search"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '设备'); input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })); box.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
    expect(client.documents).toHaveBeenLastCalledWith(expect.objectContaining({ q: '设备', offset: 0 }), expect.any(AbortSignal));
    const select = box.querySelector('select[aria-label="知识分组"]')!;
    await act(async () => { (select as HTMLSelectElement).value = '01-system'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(client.documents).toHaveBeenLastCalledWith(expect.objectContaining({ group: '01-system', offset: 0 }), expect.any(AbortSignal));
});
it('ignores an old article response after document selection changes', async () => {
    const client = makeClient();
    let resolveOld!: (value: DocumentDetail) => void;
    vi.mocked(client.document).mockImplementation(id => id === 'KB-OLD' ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve({ ...detail, id, title: '新文章', markdown: '当前正文' }));
    await render('knowledge', client, 'KB-OLD');
    await render('knowledge', client, 'KB-NEW');
    await act(async () => resolveOld({ ...detail, id: 'KB-OLD', title: '过时文章', markdown: '过时正文' }));
    expect(box.textContent).toContain('当前正文');
    expect(box.textContent).not.toContain('过时正文');
});
it('maps bundled links, preserves unbundled references, and safely opens HTTPS links', async () => {
    const doc = { ...detail, markdown: '[下一篇](next.md#part)\n\n[源码](../../src/core.cpp)\n\n[外部](https://example.com/docs)\n\n<script>bad()</script>', links: { 'next.md#part': { document_id: 'KB-NEXT', fragment: 'part' } } };
    await act(async () => root.render(<SdkDocumentReader document={doc}/>));
    expect(box.querySelector('a[href="/sdk-admin/knowledge/KB-NEXT#part"]')).toBeTruthy();
    expect(box.querySelector('a[href*="core.cpp"]')).toBeNull();
    expect(box.textContent).toContain('../../src/core.cpp');
    expect(box.querySelector('a[href^="https:"]')?.getAttribute('rel')).toContain('noopener');
    expect(box.querySelector('script')).toBeNull();
    expect(box.textContent).toContain('源码静态解读，尚未进行设备实测');
    expect(box.querySelector('.sdk-provenance dl')).toBeNull();
});
it('does not let a slow previous directory replace the selected group', async () => {
    const client = makeClient();
    let resolveOld!: (v: DocumentList) => void;
    vi.mocked(client.documents).mockImplementation(query => query.group ? Promise.resolve({ ...list, items: [{ ...detail, title: '当前分组文章' }] }) : new Promise(resolve => { resolveOld = resolve; }));
    await render('knowledge', client);
    const select = box.querySelector('select[aria-label="知识分组"]')!;
    await act(async () => { (select as HTMLSelectElement).value = '01-system'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(async () => resolveOld({ ...list, items: [{ ...detail, title: '旧目录文章' }] }));
    expect(box.textContent).toContain('当前分组文章');
    expect(box.textContent).not.toContain('旧目录文章');
});
it('shows an empty directory honestly and disables paging', async () => { const client = makeClient(); vi.mocked(client.documents).mockResolvedValue({ ...list, items: [], total: 0 }); await render('knowledge', client); expect(box.textContent).toContain('没有匹配的内容'); expect(Array.from(box.querySelectorAll('button')).find(x => x.textContent === '下一页')?.disabled).toBe(true); expect(box.querySelector('.sdk-document-list')).toBeNull(); });
it('revokes displayed content immediately when the account changes', async () => {
    const client = makeClient();
    await render('knowledge', client);
    expect(box.textContent).toContain('阅读协议');
    vi.mocked(client.access).mockImplementation(() => new Promise(() => { }));
    await act(async () => root.render(<SdkAdminPage account={{ ...account, internal_user_id: 'another-reader' }} section="knowledge" client={client}/>));
    expect(box.textContent).not.toContain('阅读协议');
});
it('reads section-specific real documents through secondary views', async () => {
    const client = makeClient();
    vi.mocked(client.documents).mockImplementation(async (query) => { const items = [{ ...detail, id: 'DOC-ANALYSIS-GIT', title: '真实 Git 分析', kind: 'analysis' as const }, { ...detail, id: 'DOC-KB-ACCEPTANCE', title: '真实静态验收', kind: 'acceptance' as const }].filter(x => x.kind === query.kind); return { ...list, items, total: items.length }; });
    await act(async () => root.render(<SdkAdminPage account={account} section="diagnostics" client={client}/>));
    expect(box.textContent).toContain('真实静态验收');
    expect(box.textContent).not.toContain('真实 Git 分析');
    await act(async () => click('过程诊断'));
    expect(box.textContent).toContain('真实 Git 分析');
    expect(box.textContent).not.toContain('真实静态验收');
});
it('creates fragment targets from headings containing inline code', async () => { await act(async () => root.render(<SdkDocumentReader document={{ ...detail, markdown: '## API `ob_start`\n\n## API `ob_start`' }}/>)); expect(box.querySelector('h2[id="api-ob_start"]')).toBeTruthy(); expect(box.querySelector('h2[id="api-ob_start-1"]')).toBeTruthy(); });
it('resolves Markdown-normalized Unicode paths through the package link map', async () => { await act(async () => root.render(<SdkDocumentReader document={{ ...detail, markdown: '[下一篇](下一篇.md#章节)', links: { '下一篇.md#章节': { document_id: 'KB-NEXT', fragment: '章节' } } }}/>)); expect(box.querySelector('a')?.getAttribute('href')).toBe('/sdk-admin/knowledge/KB-NEXT#%E7%AB%A0%E8%8A%82'); });
it('keeps native fragment scrolling for a link within the current document', async () => {
    window.history.replaceState({}, '', '/sdk-admin/knowledge/KB-NAV-004');
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, markdown: '[本节](#section)', links: { '#section': { document_id: detail.id, fragment: 'section' } } }} />));
    const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    const link = box.querySelector('a')!;
    link.addEventListener('click', e => e.preventDefault(), { once: true });
    link.dispatchEvent(event);
    const { navigate } = await import('../router');
    expect(navigate).not.toHaveBeenCalled();
    window.history.replaceState({}, '', '/');
});
it('leaves workspace navigation to the platform sidebar and retains the reading breadcrumb', async () => {
    await render('knowledge');
    expect(box.querySelector('.sdk-sidebar, nav[aria-label="SDK 工作区"]')).toBeNull();
    expect(box.querySelector('.sdk-breadcrumb')?.textContent).toContain('知识与成果');
});
it('shows catalog knowledge recommendations in order without duplicate requests or rows', async () => {
    const client = makeClient();
    const featured = ['KB-INDEX-ROOT', 'KB-NAV-004', 'KB-SYS-001', 'KB-NAV-004', 'DOC-KB-ACCEPTANCE'];
    vi.mocked(client.catalog).mockResolvedValue({ ...catalog, featured_ids: featured });
    vi.mocked(client.documents).mockImplementation(async query => ({ ...list, items: query.kind === 'acceptance' ? [{ ...detail, id: 'DOC-KB-ACCEPTANCE', title: '真实接收记录', kind: 'acceptance' }] : [], total: query.kind === 'acceptance' ? 1 : 0 }));
    vi.mocked(client.document).mockImplementation(async id => ({ ...detail, id, title: `推荐 ${id}` }));
    await render('overview', client);
    expect(Array.from(box.querySelectorAll('.sdk-document-list a')).map(a => a.getAttribute('href'))).toEqual([
        '/sdk-admin/knowledge/KB-INDEX-ROOT', '/sdk-admin/knowledge/KB-NAV-004', '/sdk-admin/knowledge/KB-SYS-001', '/sdk-admin/knowledge/DOC-KB-ACCEPTANCE'
    ]);
    expect(vi.mocked(client.document).mock.calls.map(([id]) => id)).toEqual(['KB-INDEX-ROOT', 'KB-NAV-004', 'KB-SYS-001']);
    expect(vi.mocked(client.document).mock.calls.every(([, signal]) => signal instanceof AbortSignal)).toBe(true);
});
it('keeps explicit reading starts on empty results and article pages, and locates root and metadata groups', async () => {
    const client = makeClient();
    vi.mocked(client.catalog).mockResolvedValue({ ...catalog, groups: [...catalog.groups, { id: 'root', title: '根目录', count: 1 }, { id: '_meta', title: '知识库元数据', count: 1 }] });
    vi.mocked(client.documents).mockResolvedValue({ ...list, items: [], total: 0 });
    await render('knowledge', client);
    const assertStarts = () => {
        const starts = box.querySelector('nav[aria-label="知识阅读起点"]');
        expect(starts).toBeTruthy();
        expect(starts?.querySelector('a[href="/sdk-admin/knowledge/KB-INDEX-ROOT"]')?.textContent ?? '').toContain('分层目录');
        expect(starts?.querySelector('a[href="/sdk-admin/knowledge/KB-NAV-004"]')?.textContent).toContain('阅读协议');
    };
    assertStarts();
    const select = box.querySelector<HTMLSelectElement>('select[aria-label="知识分组"]')!;
    expect(Array.from(select.options).map(option => option.value)).toEqual(['', '01-system', 'root', '_meta']);
    for (const group of ['root', '_meta']) {
        await act(async () => { select.value = group; select.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(client.documents).toHaveBeenLastCalledWith(expect.objectContaining({ group, offset: 0 }), expect.any(AbortSignal));
    }
    await render('knowledge', client, 'KB-NAV-004');
    assertStarts();
});
it('allocates unique heading fragments when repeated titles collide with suffixed titles', async () => {
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, markdown: '## A\n\n## A\n\n## A-1\n\n## A\n\n## A-1' }} />));
    const ids = Array.from(box.querySelectorAll('.sdk-markdown h2')).map(heading => heading.id);
    expect(ids).toEqual(['a', 'a-1', 'a-1-1', 'a-2', 'a-1-2']);
    expect(new Set(ids).size).toBe(ids.length);
});
it.each(['constructor', 'toString', '__proto__'])('keeps unknown state %s unverified with safe labels', async code => {
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, content_state: code, review_status: code, runtime_validation: code }} />));
    expect(box.querySelector('.sdk-reader-heading')?.textContent).toContain('状态待确认');
    await toggleDetails(true);
    const states = Array.from(box.querySelectorAll('.sdk-evidence dd')).map(field => field.textContent);
    expect(states).toEqual(['状态待确认', '状态待确认', '状态待确认']);
    expect(box.querySelector('.sdk-provenance')?.textContent).not.toContain(code);
    expect(box.querySelector('.sdk-provenance')?.textContent).not.toContain('已独立复核');
});
it('keeps an explicit evidence anchor targeted by a rewritten package link', async () => {
    const fragment = 'KB-API-012-S2-C003';
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, id: 'KB-REF-001', markdown: `[已弃用契约](../02-public-api/KB-API-012.md#${fragment})`, links: { [`../02-public-api/KB-API-012.md#${fragment}`]: { document_id: 'KB-API-012', fragment } } }} />));
    const href = box.querySelector<HTMLAnchorElement>('.sdk-markdown a')!.href;
    expect(new URL(href).pathname).toBe('/sdk-admin/knowledge/KB-API-012');
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, id: 'KB-API-012', markdown: `<a id="${fragment}"></a>\n\n## 废弃接口契约` }} />));
    const target = box.querySelector(`[id="${decodeURIComponent(new URL(href).hash.slice(1))}"]`);
    expect(target).not.toBeNull();
    expect(target?.textContent).toBe('');
    expect(target?.getAttributeNames()).toEqual(['id']);
});
it('keeps raw HTML disabled for scripts, event attributes, unsafe IDs and nonempty anchors', async () => {
    const markdown = [
        '<script>globalThis.sdkUnsafeAnchor = true</script>',
        '<a id="KB-EVENT-001" onclick="globalThis.sdkUnsafeAnchor = true"></a>',
        '<a id="KB-ESCAPE-001\" onmouseover=\"bad()"></a>',
        '<a id="javascript:bad"></a>',
        '<a id="__proto__"></a>',
        '<a id="KB-BAD ID"></a>',
        '<a id="KB-CONTENT-001">nonempty</a>',
        '<a id="KB-HREF-001" href="javascript:bad()"></a>',
        '```html\n<a id="KB-CODE-001"></a>\n```'
    ].join('\n\n');
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, markdown }} />));
    expect(box.querySelector('.sdk-markdown [id], script, [onclick], [onmouseover], a[href^="javascript:"]')).toBeNull();
    expect((globalThis as Record<string, unknown>).sdkUnsafeAnchor).toBeUndefined();
    expect(box.querySelector('.sdk-markdown pre')?.textContent).toContain('<a id="KB-CODE-001"></a>');
});
it('offers only groups belonging to the selected kind and clears group when the kind changes', async () => {
    const client = makeClient();
    const kinds = [['process', '流程与规范'], ['analysis', '分析成果'], ['design', '设计方案'], ['acceptance', '接收记录'], ['execution', '执行记录']] as const;
    vi.mocked(client.catalog).mockResolvedValue({ ...catalog, groups: [...catalog.groups, { id: 'root', title: '根目录', count: 1 }, { id: '_meta', title: '元数据', count: 1 }, ...kinds.map(([id, title]) => ({ id, title, count: 1 }))] });
    await render('knowledge', client);
    const select = box.querySelector<HTMLSelectElement>('select[aria-label="知识分组"]')!;
    expect(Array.from(select.options).map(option => option.value)).toEqual(['', '01-system', 'root', '_meta']);
    await act(async () => { select.value = 'root'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    for (const [kind, label] of kinds) {
        await act(async () => click(label));
        expect(Array.from(select.options).map(option => option.value)).toEqual(['', kind]);
        expect(select.value).toBe('');
        expect(client.documents).toHaveBeenLastCalledWith(expect.objectContaining({ kind, group: undefined, offset: 0 }), expect.any(AbortSignal));
        await act(async () => { select.value = kind; select.dispatchEvent(new Event('change', { bubbles: true })); });
    }
});

async function toggleDetails(open: boolean) {
    const details = box.querySelector<HTMLDetailsElement>('.sdk-provenance')!;
    await act(async () => { details.open = open; details.dispatchEvent(new Event('toggle')); });
}
it.each(['overview', 'products', 'delivery', 'diagnostics', 'knowledge'] as const)('keeps %s focused on readable material and available actions', async section => {
    const client = makeClient();
    vi.mocked(client.catalog).mockResolvedValue({ ...catalog, release_id: 'sdk-admin-private-release', baseline_id: 'PRIVATE-BASELINE', sources: [{ id: 'private-source', label: '内部接入验收', state: 'not_registered', description: '维护接入说明' }] });
    const ids = ['DOC-OBJECT-STATES', 'DOC-PLATFORM-PROCESS', 'DOC-RELATION-RULES', 'DOC-KB-ACCEPTANCE'];
    vi.mocked(client.documents).mockResolvedValue({ ...list, items: ids.map(id => ({ ...detail, id, title: `真实资料 ${id.slice(4)}`, group: 'raw-internal-group', kind: 'process' as const, content_state: 'design_proposal' })) });
    await render(section, client);
    expect(box.querySelector('a[href="/sdk/"]')?.textContent).toContain('禅道');
    expect(box.querySelector('.sdk-document-list a')).toBeTruthy();
    expect(box.querySelector('.sdk-scope-line, .sdk-doc-meta, .sdk-ai-note, .sdk-focus-facts, .sdk-sources')).toBeNull();
    for (const text of ['sdk-admin-private-release', 'PRIVATE-BASELINE', 'raw-internal-group', 'SDK 工具尚未接入', '内部接入验收', '接收验证', '资料快照', 'NEXT STEPS', 'DELIVERY WORKFLOW']) expect(box.textContent).not.toContain(text);
    expect(box.querySelector('.sdk-document-list')?.textContent).toContain('设计草案，不代表已实施');
    expect(box.querySelector('.sdk-document-list code')).toBeNull();
});
it.each([
    ['design_proposal', '设计草案，不代表已实施'],
    ['phase_report', '阶段性分析，结论限于文中范围'],
    ['accepted_static', '源码静态解读，尚未进行设备实测'],
] as const)('keeps the %s boundary alongside the article title and the actual body', async (content_state, description) => {
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, content_state, markdown: '## 实际正文\n\n正文原样保留 process / not_run / DOC-EXAMPLE' }} />));
    expect(box.querySelector('.sdk-reader-heading')?.textContent).toContain(description);
    expect(box.querySelector('.sdk-reader-heading')?.textContent).not.toContain(detail.id);
    expect(box.querySelector('.sdk-markdown')?.textContent).toContain('正文原样保留 process / not_run / DOC-EXAMPLE');
    expect(box.querySelector('.sdk-provenance dl, .sdk-evidence')).toBeNull();
    expect(box.textContent).not.toContain(detail.source_path);
    await toggleDetails(true);
    expect(box.querySelector('.sdk-provenance dl')).toBeTruthy();
    expect(box.querySelector('.sdk-provenance')?.textContent).toContain('代码知识');
    expect(box.querySelector('.sdk-provenance')?.textContent).toContain('设备 A');
    expect(box.querySelector('.sdk-provenance')?.textContent).toContain(detail.source_path);
    expect(box.querySelector('.sdk-provenance')?.textContent).not.toContain('accepted_static /');
    expect(box.querySelector('.sdk-provenance')?.textContent).not.toContain('independently_reviewed');
    await toggleDetails(false);
    expect(box.querySelector('.sdk-provenance dl, .sdk-evidence')).toBeNull();
});
it('uses Chinese applicability explanations without turning unchecked firmware into a verified condition', async () => {
    await act(async () => root.render(<SdkDocumentReader document={{ ...detail, conditions: { devices: ['all_applicable_to_described_component_or_data_layer'], firmware: ['not_checked_by_this_layer'], states: ['valid_SDK_handles_and_described_stream_states'] } }} />));
    await toggleDetails(true);
    const conditions = box.querySelector('.sdk-conditions')?.textContent;
    expect(conditions).toContain('设备');
    expect(conditions).toContain('适用于文中所述组件或数据层');
    expect(conditions).toContain('固件');
    expect(conditions).toContain('本层未核验固件条件');
    expect(conditions).toContain('有效的 SDK 句柄及文中规定的流状态');
    expect(conditions).not.toContain('all_applicable');
});
