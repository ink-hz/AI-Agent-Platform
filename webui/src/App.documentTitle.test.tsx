/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { IdentityDisabled, type Account } from './auth';
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

vi.mock('./pages/AccessHistoryPage', () => ({AccessHistoryPage:()=> <p>访问记录页面</p>}));
vi.mock('./pages/GovernancePage', () => ({GovernancePage:()=> <p>审计页面</p>}));
vi.mock('./pages/AgentRuntimePage', () => ({AgentRuntimePage:()=> <p>运行详情页面</p>}));
vi.mock('./pages/OverviewPage', () => ({OverviewPage:()=> <p>统一运行中心</p>}));
vi.mock('./pages/AgentDetailPage', () => ({AgentDetailPage:()=> <p>Agent详情页面</p>}));
vi.mock('./pages/ReviewPage', () => ({ReviewPage:()=> <p>任务复审页面</p>}));
vi.mock('./workspaces/fae/FaeManagementWorkspace', () => ({FaeManagementWorkspace:()=> <p>技术支持页面</p>}));

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

it('uses the destination title when an owner-only route refuses the current panorama account',async()=>{
 window.history.replaceState({panorama:true},'','/sdk-admin/knowledge/KB-API-001');
 vi.mocked(fetchPanorama).mockResolvedValue(panoramaTestData('已有全景'));
 await act(async()=>root.render(<App/>));
 expect(document.title).toBe(`文档阅读 · 知识与成果 · SDK 开发管理 · ${PLATFORM_TITLE}`);
 await act(async()=>navigate('/admin/access'));
 expect(window.history.state?.panorama).toBe(true);
 expect(box.querySelector('.permission-state')?.textContent).toContain('无权访问');
 expect(box.querySelector('.panorama-home')).toBeNull();
 expect(document.title).toBe(`访问记录 · ${PLATFORM_TITLE}`);
});

it.each([
 ['member','/sdk-admin/knowledge','SDK 文档工作区'],
 ['member','/fae/manage/','技术支持页面'],
 ['member','/admin/review?agent_id=visible','无权访问'],
 ['platform_admin','/admin/access','无权访问'],
 ['platform_admin','/admin/sessions/session-title','会话工作区'],
 ['platform_owner','/admin/access','访问记录页面'],
 ['management_viewer','/admin/governance','审计页面'],
 ['management_viewer','/admin/access','无权访问'],
 ['management_viewer','/admin/agents/visible/runtime','运行详情页面'],
 ['management_viewer','/admin/agents/hidden/runtime','无权访问'],
 ['management_viewer','/admin/review?agent_id=visible','任务复审页面'],
 ['management_viewer','/admin/review?agent_id=visible&agent_id=hidden','无权访问'],
] as const)('preserves route authorization for %s at %s',async(role,path,content)=>{
 window.history.replaceState({},'',path);
 loadAccount.mockResolvedValue({...account,role,observation_agent_ids:['visible']});
 await act(async()=>root.render(<App/>));
 expect(box.querySelector('main')?.textContent).toContain(content);
});

it('preserves the legacy-mode and absent-account boundary of the route authorization guard',async()=>{
 window.history.replaceState({},'','/admin/access');
 loadAccount.mockRejectedValue(new IdentityDisabled());
 await act(async()=>root.render(<App/>));
 expect(box.querySelector('main')?.textContent).toContain('访问记录页面');
 expect(box.querySelector('.permission-state')).toBeNull();
 expect(document.title).toBe(`访问记录 · ${PLATFORM_TITLE}`);
});

it.each(['/brain','/conversations','/conversations/c1','/missions','/missions/m1','/marketing/inbound','/marketing/voice/conversations/c1'])('pauses old platform execution at %s without execution API calls', async path => {
 window.history.replaceState({},'',path);
 await act(async()=>root.render(<App/>));
 expect(box.querySelector('main')?.textContent).toContain('功能暂未开放');
 expect(box.querySelector('main a[href="/agents"]')).not.toBeNull();
 expect(box.querySelector('textarea')).toBeNull();
 expect(vi.mocked(fetch).mock.calls.map(call=>String(call[0])).filter(url=>/api\/v1\/(conversations|brain)/.test(url))).toEqual([]);
});
it.each(['member','management_viewer'] as const)('rejects %s from the identity safety section', async role=>{
 window.history.replaceState({},'','/admin/identity/security');loadAccount.mockResolvedValue({...account,role});
 await act(async()=>root.render(<App/>));expect(box.querySelector('main')?.textContent).toContain('无权访问');expect(box.textContent).not.toContain('审计页面');
});

it('redirects the old fleet list to the canonical center while retaining detail and runtime routes', async()=>{
 window.history.replaceState({},'','/admin/agents');await act(async()=>root.render(<App/>));
 expect(window.location.pathname).toBe('/admin');expect(box.querySelector('main')?.textContent).toContain('统一运行中心');
 expect(box.querySelectorAll('.platform-sidebar [aria-current="page"]')).toHaveLength(1);
 expect(box.querySelector('.platform-sidebar [aria-current="page"]')?.getAttribute('href')).toBe('/admin');
 await act(async()=>navigate('/admin/agents/visible'));expect(box.querySelector('main')?.textContent).toContain('Agent详情页面');
 await act(async()=>navigate('/admin/agents/visible/runtime'));expect(box.querySelector('main')?.textContent).toContain('运行详情页面');
 expect(box.querySelectorAll('.platform-sidebar [aria-current="page"]')).toHaveLength(1);
 expect(box.querySelector('.platform-sidebar [aria-current="page"]')?.getAttribute('href')).toBe('/admin');
});
it('opens the manager safety deep link directly without fetching unrelated identity panels', async()=>{
 window.history.replaceState({},'','/admin/identity/security');await act(async()=>root.render(<App/>));
 expect(box.querySelector('main')?.textContent).toContain('审计页面');expect(box.querySelector('button[data-section="security"]')?.getAttribute('aria-pressed')).toBe('true');
 expect(vi.mocked(fetch).mock.calls.some(call=>String(call[0]).includes('/manage/users'))).toBe(false);
 expect(box.querySelector('.platform-sidebar [aria-current="page"]')?.getAttribute('href')).toBe('/admin/identity');
});
