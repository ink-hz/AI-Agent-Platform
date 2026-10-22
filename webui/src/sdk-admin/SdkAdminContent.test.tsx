/** @vitest-environment jsdom */
// Opt-in producer/consumer check using responses from verify-real-content.py.
import { readFileSync } from 'node:fs';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Account } from '../auth';
import { sdkAdminClient, type Catalog, type DocumentDetail, type DocumentList } from './sdkAdminApi';
import { SdkAdminPage } from './SdkAdminPage';

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
        : path === '/documents' ? data.lists_by_kind[url.searchParams.get('kind') ?? 'knowledge']
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
    expect(box.querySelectorAll('nav[aria-label="SDK 工作区"] [aria-current="page"]')).toHaveLength(1);
    window.history.replaceState({}, '', '/sdk-admin/knowledge/KB-API-001');
    await act(async () => root!.render(<SdkAdminPage section="knowledge" documentId="KB-API-001" account={account} />));
    expect(box.querySelector('article')?.textContent).toContain(data.documents['KB-API-001'].title);
    expect(box.querySelector('article')?.textContent).toContain('not_run');
    expect(box.querySelector('article a[href^="javascript:"]')).toBeNull();
    expect(box.querySelector('article a[href^="file:"]')).toBeNull();
  });
});
