/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import type { Account } from './auth';
import { aiEngineeringClient } from './aiEngineeringApi';
import { PLATFORM_TITLE } from './documentTitle';
import { navigate } from './router';
import { panoramaTestData } from './panoramaTestData';
import { fetchPanorama } from './panorama/panoramaApi';

const loadAccount = vi.hoisted(() => vi.fn());
vi.mock('./auth', async importOriginal => ({
  ...await importOriginal<typeof import('./auth')>(),
  identityShellEnabled: () => true, loadAccount,
}));
vi.mock('./api', async importOriginal => ({
  ...await importOriginal<typeof import('./api')>(),
  fetchDeployment: async () => ({mode:'local',read_only:false,freshness:'current'}),
}));
vi.mock('./aiEngineeringApi', async importOriginal => ({
  ...await importOriginal<typeof import('./aiEngineeringApi')>(),
  aiEngineeringClient: {fetchAccess:vi.fn(),fetchIndex:vi.fn(),fetchDocument:vi.fn()},
}));
vi.mock('./panorama/panoramaApi', () => ({fetchPanorama:vi.fn()}));
vi.mock('./panorama/PanoramaView', () => ({PanoramaView:({data}:any)=><h1>{data.title}</h1>}));
vi.mock('./accessEventReporter', () => ({AccessEventReporter:()=>null}));
// Page bodies have their own tests; keep the real App, routing, shell and panorama host.
vi.mock('./sdk-admin/SdkAdminPage', () => ({SdkAdminPage:()=> <p>SDK 文档工作区</p>}));
vi.mock('./pages/SessionDetailPage', () => ({SessionDetailPage:()=> <p>会话工作区</p>}));

const account:Account={internal_user_id:'title-owner',display_name:'管理员',role:'platform_admin',departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'};
let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;
beforeEach(()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 box=document.createElement('div');document.body.append(box);root=createRoot(box);
 window.history.replaceState({},'','/');document.title='';
 vi.resetAllMocks();
 loadAccount.mockResolvedValue(account);
 vi.mocked(aiEngineeringClient.fetchAccess).mockResolvedValue({allowed:true});
 vi.stubGlobal('requestAnimationFrame',vi.fn());
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({allowed:true}),{status:200})));
});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();document.title='';vi.restoreAllMocks();vi.unstubAllGlobals();});

it.each([
 ['/sdk-admin/knowledge/KB-API-001',`文档阅读 · 知识与成果 · SDK 开发管理 · ${PLATFORM_TITLE}`,'SDK 文档工作区'],
 ['/admin/sessions/session-title',`会话回放 · ${PLATFORM_TITLE}`,'会话工作区'],
])('preserves the refreshed workspace title through asynchronous panorama loading and restores the graph title from %s',async(path,title,body)=>{
 window.history.replaceState({panorama:true},'',path);
 let resolvePanorama!:(value:ReturnType<typeof panoramaTestData>)=>void;
 vi.mocked(fetchPanorama).mockReturnValue(new Promise(resolve=>{resolvePanorama=resolve;}));
 await act(async()=>root.render(<App/>));
 expect(document.title).toBe(title);expect(box.textContent).toContain(body);
 await act(async()=>resolvePanorama(panoramaTestData('异步全景标题')));
 expect(document.title).toBe(title);expect(box.textContent).toContain(body);
 await act(async()=>Array.from(box.querySelectorAll('button')).find(button=>button.textContent==='回到全景')!.click());
 expect(window.location.pathname).toBe('/');
 expect(document.title).toBe(`异步全景标题 · ${PLATFORM_TITLE}`);
});

it('retains App title ownership while loading and when the account cannot render a panorama host',async()=>{
 window.history.replaceState({panorama:true},'','/sdk-admin/knowledge/KB-API-001');
 let resolveAccount!:(value:Account)=>void;
 loadAccount.mockReturnValue(new Promise(resolve=>{resolveAccount=resolve;}));
 await act(async()=>root.render(<App/>));
 expect(box.textContent).toContain('正在进入 Agent Platform');
 expect(document.title).toBe(`文档阅读 · 知识与成果 · SDK 开发管理 · ${PLATFORM_TITLE}`);
 await act(async()=>resolveAccount({...account,role:'member'}));
 expect(box.textContent).toContain('无权限');expect(fetchPanorama).not.toHaveBeenCalled();
 await act(async()=>navigate('/admin/sessions/denied-session'));
 expect(box.textContent).toContain('无权限');
 expect(document.title).toBe(`会话回放 · ${PLATFORM_TITLE}`);
});

it('returns title ownership to App when panorama access is denied',async()=>{
 window.history.replaceState({panorama:true},'','/sdk-admin/knowledge/KB-API-001');
 vi.mocked(aiEngineeringClient.fetchAccess).mockResolvedValue({allowed:false});
 await act(async()=>root.render(<App/>));
 expect(box.textContent).toContain('无权限');expect(fetchPanorama).not.toHaveBeenCalled();
 expect(document.title).toBe(`文档阅读 · 知识与成果 · SDK 开发管理 · ${PLATFORM_TITLE}`);
 await act(async()=>navigate('/organization'));
 expect(box.textContent).toContain('无权限');
 expect(document.title).toBe(`组织架构 · ${PLATFORM_TITLE}`);
});
