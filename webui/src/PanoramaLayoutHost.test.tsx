/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useRoute, navigate } from './router';
import { SdkAdminPage } from './sdk-admin/SdkAdminPage';
import type { SdkAdminClient } from './sdk-admin/sdkAdminApi';
import { AppShell } from './AppShell';
import { AiEngineeringLanding } from './pages/AiEngineeringPage';
import { AiEngineeringApiError } from './aiEngineeringApi';
import { allowPanoramaNavigation, workspaceGroup } from './panoramaNavigation';
import { PLATFORM_TITLE, routeDocumentTitle } from './documentTitle';
import { fetchPanorama } from './panorama/panoramaApi';
import { panoramaTestData } from './panoramaTestData';
vi.mock('./api', () => ({fetchDeployment: async () => ({mode:'cloud-replica',read_only:true,freshness:'current'})}));
vi.mock('./panorama/panoramaApi', () => ({fetchPanorama: vi.fn(async () => panoramaTestData())}));
// Test the session boundary; the editor has its own API and component tests.
vi.mock('./panorama/PanoramaView', () => ({PanoramaView: (props:any) => <section>
  <h1>{props.data.title}</h1>
  <button onClick={() => props.onDataChange(panoramaTestData('已发布的新布局'))}>发布完成</button>
  <button onClick={() => props.onDirtyChange(true)}>本地修改</button>
  <button onClick={() => props.onDirtyChange(false)}>保存完成</button>
  <button onClick={() => props.onAuthorizationFailure(new AiEngineeringApiError(401))}>会话失效</button>
</section>}));
const account:any={internal_user_id:'admin',role:'platform_admin',display_name:'管理员',departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'};
let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;
beforeEach(() => {
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 box=document.createElement('div');document.body.append(box);root=createRoot(box);
 window.history.replaceState({},'', '/');
 vi.mocked(fetchPanorama).mockReset().mockResolvedValue(panoramaTestData());
});
afterEach(async () => {await act(async()=>root.unmount());box.remove();vi.restoreAllMocks();vi.unstubAllGlobals();});
async function setup(){
 const onNavigate=vi.fn();const client:any={fetchAccess:vi.fn().mockResolvedValue({allowed:true}),fetchIndex:vi.fn(),fetchDocument:vi.fn()};
 await act(async()=>root.render(<AppShell panorama route={{name:"home"}} account={account}><AiEngineeringLanding account={account} client={client} fallback={<p>无权访问</p>} onNavigate={onNavigate}/></AppShell>));
 return onNavigate;
}
async function click(label:string){await act(async()=>[...box.querySelectorAll('button')].find(b=>b.textContent===label)!.click());}
it('adopts the published graph returned by the editor without reloading the host',async()=>{
 await setup();await click('发布完成');expect(box.textContent).toContain('已发布的新布局');
});
it('guards unsaved layout navigation and unload; saving releases that guard',async()=>{
 await setup();await click('本地修改');vi.spyOn(window,'confirm').mockReturnValue(false);
 const warn=new Event('beforeunload',{cancelable:true});window.dispatchEvent(warn);expect(warn.defaultPrevented).toBe(true);
 expect(allowPanoramaNavigation('/brain')).toBe(false);expect(allowPanoramaNavigation('/')).toBe(true);
 expect(allowPanoramaNavigation('/organization')).toBe(true);
 await click('保存完成');
 const clean=new Event('beforeunload',{cancelable:true});window.dispatchEvent(clean);expect(clean.defaultPrevented).toBe(false);
 expect(allowPanoramaNavigation('/brain')).toBe(true);
});
it('removes protected graph and starts existing login on editor authorization failure',async()=>{
 const navigate=await setup();await click('会话失效');
 expect(box.textContent).not.toContain('测试全景');expect(box.textContent).toContain('无权访问');
 expect(navigate).toHaveBeenCalledWith('/login?return_path=%2F');
 expect(box.querySelector('nav[aria-label="平台功能"]')).toBeNull();
});
it('keeps normal platform navigation outside the panorama content',async()=>{
 const navigate=await setup();const nav=box.querySelector('.platform-sidebar')!;
 expect(box.querySelector('.panorama-home .topbar')).toBeNull();expect(box.querySelector('.platform-functions')).toBeNull();
 expect(nav).not.toBeNull();expect(nav.compareDocumentPosition(box.querySelector('h1')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 await act(async()=>nav.querySelector<HTMLAnchorElement>('a[href="/admin"]')!.click());
 expect(window.location.pathname).toBe('/admin');
});
it('preserves unsaved layout protection when leaving through a business management shortcut',async()=>{
 const navigate=await setup();await click('本地修改');vi.spyOn(window,'confirm').mockReturnValue(false);
 await act(async()=>box.querySelector<HTMLAnchorElement>('.platform-sidebar a[href="/admin"]')!.click());
 expect(window.confirm).toHaveBeenCalledTimes(1);expect(navigate).not.toHaveBeenCalled();expect(window.location.pathname).toBe('/');
});

it('keeps platform entry points available if only the panorama data request fails',async()=>{
 vi.mocked(fetchPanorama).mockRejectedValue(new Error('panorama unavailable'));
 const navigate=await setup();expect(box.textContent).toContain('AI 工程全景暂时不可用');
 await act(async()=>box.querySelector<HTMLAnchorElement>('.platform-sidebar a[href="/admin"]')!.click());
 expect(window.location.pathname).toBe('/admin');
});

it('keeps SDK search, article and return links in the shared host without draft or unload prompts',async()=>{
 window.history.replaceState({panorama:true},'', '/sdk-admin/knowledge');
 vi.stubGlobal('requestAnimationFrame',vi.fn());
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({allowed:true}),{status:200})));
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 const document={id:'KB-API-001',title:'真实组件阅读',kind:'knowledge',group:'01-system',markdown:'# 阅读正文',conditions:{},links:{}};
 const sdkClient:SdkAdminClient={access:vi.fn().mockResolvedValue({allowed:true}),catalog:vi.fn().mockResolvedValue({generated_at:'2026-10-22',counts:{knowledge_pages:1},groups:[]}),documents:vi.fn().mockResolvedValue({items:[document],total:1}),document:vi.fn().mockResolvedValue(document)};
 const panoramaClient:any={fetchAccess:vi.fn().mockResolvedValue({allowed:true})};
 function Host(){
  const route=useRoute();
  return <AppShell panorama route={route} account={account}><AiEngineeringLanding account={account} client={panoramaClient} fallback={<p>无权访问</p>} onNavigate={navigate} workspaceRoute={route.name==='home'?undefined:route} renderWorkspace={workspace=>workspace.name==='sdk-admin'?<SdkAdminPage section={workspace.section} documentId={workspace.documentId} account={account} client={sdkClient}/>:<p>其他工作区</p>}/></AppShell>;
 }
 await act(async()=>root.render(<Host/>));
 const input=box.querySelector('input[type="search"]')!;
 await act(async()=>{
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'设备');
  input.dispatchEvent(new Event('input',{bubbles:true}));
  box.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
 });
 expect(sdkClient.documents).toHaveBeenLastCalledWith(expect.objectContaining({q:'设备'}),expect.any(AbortSignal));
 const workarea=box.querySelector('.panorama-work-area');
 await act(async()=>box.querySelector<HTMLAnchorElement>('.sdk-document-list a')!.click());
 expect(window.location.pathname).toBe('/sdk-admin/knowledge/KB-API-001');
 expect(window.history.state?.panorama).toBe(true);
 expect(box.querySelector('.panorama-work-area')).toBe(workarea);
 expect(box.querySelector('article.sdk-reader')?.textContent).toContain('阅读正文');
 expect(box.querySelectorAll('.platform-sidebar [aria-current="page"]')).toHaveLength(1);
 await act(async()=>box.querySelector<HTMLAnchorElement>('.sdk-back')!.click());
 expect(window.location.pathname).toBe('/sdk-admin/knowledge');
 expect(window.history.state?.panorama).toBe(true);expect(box.querySelector('.panorama-work-area')).toBe(workarea);
 await act(async()=>box.querySelector<HTMLAnchorElement>('.platform-sidebar a[href="/brain"]')!.click());
 expect(window.location.pathname).toBe('/brain');expect(box.textContent).toContain('其他工作区');
 const unload=new Event('beforeunload',{cancelable:true});window.dispatchEvent(unload);
 expect(unload.defaultPrevented).toBe(false);expect(confirm).not.toHaveBeenCalled();
});

it.each(['/sdk-admin/knowledge/KB-API-001','/admin/sessions/session-title'])('keeps the refreshed workspace title after panorama resolves asynchronously at %s',async(path)=>{
 window.history.replaceState({panorama:true},'',path);
 vi.stubGlobal('requestAnimationFrame',vi.fn());
 let resolvePanorama!:(value:ReturnType<typeof panoramaTestData>)=>void;
 vi.mocked(fetchPanorama).mockReturnValue(new Promise(resolve=>{resolvePanorama=resolve;}));
 const client:any={fetchAccess:vi.fn().mockResolvedValue({allowed:true})};
 function Host(){
  const route=useRoute();
  return <AppShell panorama route={route} account={account}><AiEngineeringLanding account={account} client={client} direct fallback={null} onNavigate={navigate} workspaceRoute={workspaceGroup(route)?route:undefined} renderWorkspace={workspace=><p>{routeDocumentTitle(workspace)}</p>}/></AppShell>;
 }
 await act(async()=>root.render(<Host/>));
 const expectedTitle=path.startsWith('/sdk-admin')?`文档阅读 · 知识与成果 · SDK 开发管理 · ${PLATFORM_TITLE}`:`会话回放 · ${PLATFORM_TITLE}`;
 expect(document.title).toBe(expectedTitle);
 expect(fetchPanorama).toHaveBeenCalledTimes(1);
 await act(async()=>resolvePanorama(panoramaTestData('异步加载的全景')));
 expect(document.title).toBe(expectedTitle);
 expect(box.querySelector('.panorama-work-area')?.textContent).toContain(expectedTitle);
 await click('回到全景');
 expect(window.location.pathname).toBe('/');
 expect(document.title).toBe(`异步加载的全景 · ${PLATFORM_TITLE}`);
});
