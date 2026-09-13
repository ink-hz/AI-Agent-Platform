/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrResearchWorkspace, ResearchMarkdown } from "./HrResearchWorkspace";
import type { Account } from "../../auth";
const account = { internal_user_id: "reader", csrf_token: "token" } as Account;
const article = { id: "insta360--q9", title: "Q9 影石技术意图", excerpt: "设备约束与创作体验", question: 9, companies: ["insta360"], kind: "analysis" };
const catalog = { edition: "edition-1", analyzed_at: "2026-09-09", observed_at: "2026-09-06", covered_job_identities: 3355, companies: [{ id: "insta360", name: "影石" }], questions: [{ id: 9, name: "技术意图" }], articles: [article] };
const document = { ...article, edition: "edition-1", markdown: '# 影石技术意图\n\n正文判断。\n\n<details><summary>查看原文依据</summary>\n\n> 岗位原文\n\n[后续研究](next.md)\n\n</details>\n\n<script>alert(1)</script>\n\n[危险](javascript:alert(1))', links: { "next.md": "next" } };
let container: HTMLDivElement; let root: Root;
beforeEach(() => { (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true; history.replaceState({}, "", "/hr/panorama"); container = documentNode(); root = createRoot(container); });
function documentNode() { const node = window.document.createElement("div"); window.document.body.append(node); return node; }
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
it("opens the pinned article, renders evidence and returns without losing the company filter", async () => {
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("research?") || url.endsWith("/research") ? catalog : document), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => root.render(<HrResearchWorkspace account={account} />));
  await act(async () => (container.querySelectorAll('aside button')[1] as HTMLButtonElement).click());
  await act(async () => (container.querySelector('.hr-research-card') as HTMLAnchorElement).click());
  expect(fetcher.mock.calls[fetcher.mock.calls.length - 1]?.[0]).toContain('edition=edition-1');
  expect(container.querySelector('details summary')?.textContent).toBe('查看原文依据');
  expect(container.querySelector('details')?.textContent).toContain('岗位原文');
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  await act(async () => (container.querySelector('.hr-research-back') as HTMLButtonElement).click());
  expect(location.search).toContain('research_company=insta360');
  expect(container.querySelector('.hr-research-card')).not.toBeNull();
});
it.each([401, 403])('clears private content and handles %s correctly', async status => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status })));
  await act(async () => root.render(<HrResearchWorkspace account={account} />));
  expect(container.querySelector('.hr-research-card')).toBeNull();
  expect(container.textContent).toContain(status === 401 ? '登录状态已失效' : '没有 HR 情报权限');
  expect(Boolean(container.querySelector('a[href*="login"]'))).toBe(status === 401);
  if (status === 401) expect(container.querySelector('a[href*="login"]')?.getAttribute('href')).toContain('return_path=');
});
it('keeps a missing edition explicit instead of displaying current content', async () => {
  history.replaceState({}, '', '/hr/panorama?research=insta360--q9&edition=missing');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(catalog), { status: url.endsWith('/research') ? 200 : 404 })));
  await act(async () => root.render(<HrResearchWorkspace account={account} />));
  expect(container.textContent).toContain('指定版本暂不可用');
  expect(container.querySelector('article')).toBeNull();
});
it('turns an internal evidence link into edition-preserving navigation', async () => {
  const open = vi.fn();
  await act(async () => root.render(<ResearchMarkdown document={document} onOpen={open} />));
  const link = container.querySelector('a[href*="research=next"]') as HTMLAnchorElement;
  expect(link.href).toContain('edition=edition-1');
  await act(async () => link.click());
  expect(open).toHaveBeenCalledWith('next');
});
it('opens a uniquely resolved source link at its original source edition', async () => {
  const linked = { ...document, markdown: '[原岗位](https://example.com/job)', source_reference: {edition:'source-1',source_bundle_id:'original',links:{'https://example.com/job':{company_key:'insta360',job_id:'job-1'}}} };
  await act(async () => root.render(<ResearchMarkdown document={linked} onOpen={vi.fn()} />));
  const link = container.querySelector('a') as HTMLAnchorElement;
  expect(link.href).toContain('source_edition=source-1');
  expect(link.href).toContain('source_job=job-1');
  await act(async () => link.click());
  expect(location.search).toContain('layer=sources');
  expect(location.search).toContain('research_company=insta360');
});
