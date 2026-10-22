/** @vitest-environment jsdom */
// Opt-in producer/consumer check using responses from verify-real-content.py.
import { readFileSync } from 'node:fs';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Account } from '../auth';
import { sdkAdminClient, type Catalog, type DocumentDetail, type DocumentList } from './sdkAdminApi';
import { SdkAdminPage } from './SdkAdminPage';
import { SdkDocumentReader } from './SdkDocumentReader';

const fixturePath = process.env.SDK_ADMIN_HTTP_FIXTURE;
const suite = fixturePath ? describe : describe.skip;
let root: ReturnType<typeof createRoot> | undefined;
let box: HTMLDivElement | undefined;
afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  box?.remove(); root = undefined; box = undefined;
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});

suite('SDK real content HTTP response to frontend contract', () => {
  function install() {
    const data = JSON.parse(readFileSync(fixturePath!, 'utf8')) as {
      catalog: Catalog; documents: Record<string, DocumentDetail>;
      knowledge_page: DocumentList; lists_by_kind: Record<string, DocumentList>;
    };
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      const url = new URL(input, 'https://local.test');
      const path = url.pathname.replace('/api/v1/sdk-admin', '');
      const body = path === '/access' ? { allowed: true } : path === '/catalog' ? data.catalog
        : path === '/documents' ? (url.searchParams.get('kind') === 'knowledge' && url.searchParams.get('limit') === '30' ? data.knowledge_page : data.lists_by_kind[url.searchParams.get('kind') ?? 'knowledge'])
        : data.documents[path.replace('/documents/', '')];
      return new Response(JSON.stringify(body), { status: body ? 200 : 404 });
    }));
    return data;
  }

  it('accepts the real catalog, lists and knowledge/reference documents through the API client', async () => {
    const data = install();
    expect((await sdkAdminClient.catalog()).counts).toEqual({ knowledge_pages: 865, knowledge_navigation: 19, documents: 893 });
    for (const kind of Object.keys(data.lists_by_kind)) {
      const list = await sdkAdminClient.documents({ kind: kind as 'knowledge', limit: 100 });
      expect(list).toEqual(data.lists_by_kind[kind]);
    }
    for (const id of Object.keys(data.documents)) expect(await sdkAdminClient.document(id)).toEqual(data.documents[id]);
  });

  it.each(['overview', 'products', 'delivery', 'diagnostics', 'knowledge'] as const)('keeps real %s defaults free of maintenance metadata', async section => {
    const data = install();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const account = { internal_user_id: 'sdk-default-reader', display_name: 'SDK reader' } as Account;
    box = document.createElement('div'); document.body.append(box); root = createRoot(box);
    await act(async () => root!.render(<SdkAdminPage section={section} account={account} />));
    expect(box.querySelector('.sdk-document-list a')).not.toBeNull();
    expect(box.querySelector('.sdk-scope-line, .sdk-doc-meta, .sdk-ai-note, .sdk-focus-facts, .sdk-sources')).toBeNull();
    expect(box.textContent).not.toContain(data.catalog.release_id);
    expect(box.textContent).not.toContain(data.catalog.baseline_id);
    expect(box.textContent).not.toContain('not_run');
    expect(box.querySelector('.sdk-document-list')?.textContent).not.toMatch(/设备实测|运行验证|设备表现/);
    expect(box.querySelector('.sdk-breadcrumb')).toBeNull();
    expect(box.textContent?.split('需求与进度在禅道查看')).toHaveLength(2);
    expect(box.querySelector('a[href="/sdk/"]')).not.toBeNull();
    if (section === 'products' || section === 'delivery') expect(box.querySelector('.sdk-document-list')?.textContent).toContain('流程与规范 · 草稿');
  });

  it.each([
    ['delivery', '开发与集成', ['流程与规范', '分析成果']],
    ['delivery', '准出与交付', ['执行记录', '流程与规范']],
    ['diagnostics', '改进行动', ['执行记录', '设计方案']],
  ] as const)('keeps real %s / %s lists concise by purpose', async (section, title, types) => {
    install();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const account = { internal_user_id: 'sdk-stage-reader', display_name: 'SDK reader' } as Account;
    box = document.createElement('div'); document.body.append(box); root = createRoot(box);
    await act(async () => root!.render(<SdkAdminPage section={section} account={account} />));
    const tab = Array.from(box.querySelectorAll<HTMLButtonElement>('.sdk-tabs button')).find(button => button.textContent?.endsWith(title))!;
    await act(async () => tab.click());
    const list = box.querySelector('.sdk-document-list')!;
    for (const type of types) expect(list.textContent).toContain(type);
    expect(list.textContent).not.toMatch(/设备实测|设备表现|运行验证|not_run|不代表|结论限于/);
    if (types.some(type => type === '分析成果')) expect(list.textContent).toContain('分析成果 · 阶段分析');
    if (types.some(type => type === '流程与规范')) expect(list.textContent).toContain('流程与规范 · 草稿');
  });

  it('exposes real layered entry points and reads a real article with its evidence labels', async () => {
    const data = install();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const account: Account = { internal_user_id: 'sdk-fixture', display_name: 'SDK reader', role: 'member',
      departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [],
      directory_freshness: 'fresh', hard_stale_read_only: false, csrf_token: 'fixture' };
    box = document.createElement('div'); document.body.append(box); root = createRoot(box);
    await act(async () => root!.render(<SdkAdminPage section="overview" account={account} />));
    expect(box.querySelector('a[href="/sdk-admin/knowledge/KB-INDEX-ROOT"]')).not.toBeNull();
    expect(box.querySelector('a[href="/sdk-admin/knowledge/KB-NAV-004"]')).not.toBeNull();
    expect(box.querySelector('nav[aria-label="SDK 工作区"]')).toBeNull();
    window.history.replaceState({}, '', '/sdk-admin/knowledge/KB-API-001');
    await act(async () => root!.render(<SdkAdminPage section="knowledge" documentId="KB-API-001" account={account} />));
    expect(box.querySelector('article')?.textContent).toContain(data.documents['KB-API-001'].title);
    expect(box.querySelector('.sdk-reader-heading')?.textContent).toContain('基于源码整理，设备表现需结合实测');
    expect(box.querySelector('.sdk-provenance dl, .sdk-evidence')).toBeNull();
    expect(box.querySelector('article a[href^="javascript:"]')).toBeNull();
    expect(box.querySelector('article a[href^="file:"]')).toBeNull();
    const fragment = 'KB-API-012-S2-C003';
    // These are actual authorized HTTP responses from the frozen release.
    const api012 = await sdkAdminClient.document('KB-API-012');
    expect(api012.markdown).toContain(`<a id="${fragment}"></a>`);
    const reference = await sdkAdminClient.document('KB-REF-001');
    expect(Object.values(reference.links)).toContainEqual({ document_id: 'KB-API-012', fragment });
    await act(async () => root!.render(<SdkAdminPage section="knowledge" documentId="KB-REF-001" account={account} />));
    const links = box.querySelectorAll(`article a[href="/sdk-admin/knowledge/KB-API-012#${fragment}"]`);
    expect(links).toHaveLength(4);
    const targetId = new URL((links[0] as HTMLAnchorElement).href).hash.slice(1);
    window.history.replaceState({}, '', `/sdk-admin/knowledge/KB-API-012#${targetId}`);
    await act(async () => root!.render(<SdkAdminPage section="knowledge" documentId="KB-API-012" account={account} />));
    expect(box.querySelector('article')?.textContent).toContain(api012.title);
    expect(box.querySelector(`article [id="${decodeURIComponent(targetId)}"]`)).not.toBeNull();
  });

  it('renders the real status analysis paragraphs and both evidence tables inside horizontal scroll containers', async () => {
    const data = install();
    const status = data.documents['DOC-ANALYSIS-STATUS'];
    expect(status.title).toBe('SDK阶段性现状盘点（2026-10-22）');
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const account = { internal_user_id: 'sdk-status-reader', display_name: 'SDK reader' } as Account;
    box = document.createElement('div'); document.body.append(box); root = createRoot(box);
    window.history.replaceState({}, '', '/sdk-admin/knowledge/DOC-ANALYSIS-STATUS');
    await act(async () => root!.render(<SdkAdminPage section="knowledge" documentId="DOC-ANALYSIS-STATUS" account={account} />));
    const article = box.querySelector('article.sdk-reader')!;
    expect(article.querySelector('.sdk-reader-heading h1')?.textContent).toBe(status.title);
    expect(article.querySelector('.sdk-reader-heading')?.textContent).toContain('阶段性分析，结论限于文中范围');
    expect(article.querySelector('.sdk-provenance dl, .sdk-evidence')).toBeNull();
    const paragraphs = Array.from(article.querySelectorAll('.sdk-markdown p')).map(p => p.textContent);
    expect(paragraphs.some(p => p?.startsWith('现有证据能定位部分实现'))).toBe(true);
    expect(paragraphs.some(p => p?.includes('不是额外平台扩项或工期承诺'))).toBe(true);
    const tables = article.querySelectorAll('.sdk-table-scroll > table');
    expect(tables).toHaveLength(2);
    expect(Array.from(tables).map(table => table.querySelectorAll('tbody tr').length)).toEqual([12, 4]);
    expect(article.querySelectorAll('.sdk-table-scroll tbody td')).toHaveLength(44);
    expect(tables[0].querySelector('thead')?.textContent).toContain('固定槽位');
    expect(tables[0].textContent).toContain('P12 ROS/ROS2');
    expect(tables[1].textContent).toContain('建立两版本发布清单');
  });

});

suite('SDK real document metadata presentation by purpose', () => {
  const data = fixturePath ? JSON.parse(readFileSync(fixturePath, 'utf8')) as {
    documents: Record<string, DocumentDetail>; lists_by_kind: Record<string, DocumentList>;
  } : undefined;
  const cases = data ? Object.values(data.lists_by_kind).flatMap(list => list.items).filter(doc => doc.kind !== 'knowledge') : [];
  it.each(cases)('keeps $kind $id article metadata free of unrelated runtime copy', async summary => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    box = document.createElement('div'); document.body.append(box); root = createRoot(box);
    // Header-only cases use actual response metadata, without inventing article content.
    const doc = data!.documents[summary.id] ?? { ...summary, markdown: '', sha256: summary.source_sha256, links: {}, conditions: {} };
    await act(async () => root!.render(<SdkDocumentReader document={doc} />));
    const heading = box.querySelector('.sdk-reader-heading')!.textContent;
    expect(heading).not.toMatch(/设备实测|设备表现|运行验证/);
    if (summary.content_state === 'design_proposal') expect(heading).toContain('设计草案，不代表已实施');
    if (summary.content_state === 'phase_report') expect(heading).toContain('阶段性分析，结论限于文中范围');
    expect(box.querySelector('.sdk-provenance dl')).toBeNull();
  });
  it.each(['KB-INDEX-ROOT', 'KB-NAV-004'])('does not describe navigation %s as source implementation evidence', async id => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    box = document.createElement('div'); document.body.append(box); root = createRoot(box);
    await act(async () => root!.render(<SdkDocumentReader document={data!.documents[id]} />));
    expect(box.querySelector('.sdk-reader-heading')!.textContent).not.toMatch(/设备实测|设备表现|运行验证|基于源码/);
  });
});
