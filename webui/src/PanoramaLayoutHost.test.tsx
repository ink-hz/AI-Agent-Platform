/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppShell } from './AppShell';
import { AiEngineeringLanding } from './pages/AiEngineeringPage';
import { AiEngineeringApiError } from './aiEngineeringApi';
import { allowPanoramaNavigation } from './panoramaNavigation';
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
afterEach(async () => {await act(async()=>root.unmount());box.remove();vi.restoreAllMocks();});
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
