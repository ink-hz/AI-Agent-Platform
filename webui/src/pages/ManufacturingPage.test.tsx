/** @vitest-environment jsdom */
import { StrictMode, act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Account } from '../auth';
import { ManufacturingPage } from './ManufacturingPage';
import { navigate, parseRoute, useRoute } from '../router';
import { allowPanoramaNavigation } from '../panoramaNavigation';
import { routeDocumentTitle } from '../documentTitle';
const account: Account = {internal_user_id:'admin',display_name:'Admin',role:'platform_admin',departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'trusted-csrf'};
let box: HTMLDivElement, root: ReturnType<typeof createRoot>, release=0;
let mount: ReturnType<typeof vi.fn>, unmount: ReturnType<typeof vi.fn>;
let options: any;
beforeEach(()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 window.history.replaceState({},'', '/manufacturing');vi.stubGlobal('requestAnimationFrame',vi.fn());
 unmount=vi.fn(); mount=vi.fn((_element,opts)=>{ options=opts;return {unmount,isDirty:()=>false}; });
 (window as any).OrbbecManufacturing={mount};
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:200,json:async()=>({allowed:true,available:true,can_edit:true,release:`test-${++release}`})}));
 box=document.createElement('div');document.body.append(box);root=createRoot(box);
});
afterEach(async()=>{
 await act(async()=>root.unmount());box.remove();document.querySelectorAll('[data-manufacturing-asset]').forEach(node=>node.remove());
 delete (window as any).OrbbecManufacturing;vi.restoreAllMocks();vi.unstubAllGlobals();
});
async function render(value=account,strict=false){await act(async()=>root.render(strict?<StrictMode><ManufacturingPage account={value}/></StrictMode>:<ManufacturingPage account={value}/>));}
async function assets(type='load') {await act(async()=>{document.querySelectorAll('[data-manufacturing-asset]').forEach(node=>node.dispatchEvent(new Event(type)));});}
it('recognizes only native paths and validated login returns',async()=>{
 expect(parseRoute('/manufacturing')).toEqual({name:'manufacturing'});expect(parseRoute('/manufacturing/')).toEqual({name:'manufacturing'});
 for(const path of ['/manufacturing/secret','/manufacturing//','/manufacturing%2f']) expect(parseRoute(path)).toEqual({name:'not-found'});
 expect(parseRoute('/manufacturing','?line=secret')).toEqual({name:'not-found'});
 expect(routeDocumentTitle({name:'manufacturing'})).toContain('大制造');
 const {loginReturnPath}=await import('../auth');expect(loginReturnPath('?return_path=%2Fmanufacturing%2F')).toBe('/manufacturing/');
});
it('mounts with exact prefix/CSRF/readOnly and cleans up under StrictMode',async()=>{
 window.history.replaceState({},'', '/_preview/dingtalk-r1/manufacturing');await render({...account,hard_stale_read_only:true},true);
 expect(fetch).toHaveBeenCalledWith('/_preview/dingtalk-r1/api/v1/manufacturing/access',expect.objectContaining({credentials:'same-origin'}));
 expect(document.querySelectorAll('script[data-manufacturing-asset]')).toHaveLength(1);await assets();expect(mount).toHaveBeenCalledTimes(1);
 expect(options).toMatchObject({apiBase:'/_preview/dingtalk-r1/api/v1/manufacturing',csrfToken:'trusted-csrf',readOnly:true});
 expect(mount.mock.calls[0][0]).toBe(box.querySelector('[data-manufacturing-root]'));
 await act(async()=>root.render(<p>Retired</p>));expect(unmount).toHaveBeenCalledTimes(1);options.onDirtyChange(true);expect(allowPanoramaNavigation('/brain')).toBe(true);
});
it.each([401,403,503])('shows safe access failure %s without assets',async(status)=>{
 vi.mocked(fetch).mockResolvedValue({ok:false,status} as Response);await render();expect(box.querySelector('[role="alert"]')).not.toBeNull();
 expect(document.querySelector('[data-manufacturing-asset]')).toBeNull();expect(mount).not.toHaveBeenCalled();
});
it('shows unavailable without fake UI',async()=>{
 vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>({allowed:true,available:false})} as Response);
 await render();expect(box.textContent).toContain('暂不可用');expect(mount).not.toHaveBeenCalled();
});
it('reports asset and mount errors and can retry',async()=>{
 await render();await assets('error');expect(box.textContent).toContain('加载失败');await act(async()=>box.querySelector('button')!.click());
 mount.mockImplementation(()=>{throw new Error('broken mount');});await assets();expect(box.textContent).toContain('加载失败');expect(allowPanoramaNavigation('/brain')).toBe(true);
});
it('protects SPA/popstate and retires dirty/auth callbacks',async()=>{
 function Host(){useRoute();return <ManufacturingPage account={account}/>;}
 await act(async()=>root.render(<Host/>));await assets();const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 options.onDirtyChange(true);navigate('/brain');expect(window.location.pathname).toBe('/manufacturing');
 window.history.pushState({},'', '/missions');window.dispatchEvent(new PopStateEvent('popstate'));expect(window.location.pathname).toBe('/manufacturing');expect(confirm).toHaveBeenCalledTimes(2);
 await act(async()=>options.onAuthError());expect(unmount).toHaveBeenCalledTimes(1);expect(box.textContent).toContain('无权访问');expect(allowPanoramaNavigation('/brain')).toBe(true);
 options.onDirtyChange(true);options.onAuthError();expect(unmount).toHaveBeenCalledTimes(1);
});
it('ignores access resolving after retirement',async()=>{
 let resolve!: (response:Response)=>void;vi.mocked(fetch).mockImplementation(()=>new Promise(value=>{resolve=value;}));
 await render();await act(async()=>root.render(<p>Retired</p>));await act(async()=>resolve({ok:true,status:200,json:async()=>({allowed:true,available:true,release:'late'})} as Response));
 expect(mount).not.toHaveBeenCalled();expect(document.querySelector('[data-manufacturing-asset]')).toBeNull();
});
it('uses the library heading when mounted',async()=>{
 mount.mockImplementation((element,opts)=>{options=opts;element.innerHTML='<h1>大制造</h1>';return {unmount,isDirty:()=>false};});
 await render();await assets();expect(box.querySelectorAll('h1')).toHaveLength(1);
});
it('loads each asset URL once across route re-entry',async()=>{
 vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>({allowed:true,available:true,can_edit:true,release:'shared-release'})} as Response);
 await render();await assets();await act(async()=>root.render(<p>Retired</p>));await render();
 expect(document.querySelectorAll('script[data-manufacturing-asset]')).toHaveLength(1);expect(document.querySelectorAll('link[data-manufacturing-asset]')).toHaveLength(1);
 expect(mount).toHaveBeenCalledTimes(2);
});
it('binds cached script and active CSS to v1→v2→v1 release transitions',async()=>{
 const first=vi.fn(()=>({unmount:vi.fn(),isDirty:()=>false})), second=vi.fn(()=>({unmount:vi.fn(),isDirty:()=>false}));
 async function select(release:string,csrf:string){
  vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>({allowed:true,available:true,can_edit:true,release})} as Response);
  await render({...account,csrf_token:csrf});
 }
 await select('rollback-v1','first');window.OrbbecManufacturing={mount:first};await assets();
 await select('rollback-v2','second');window.OrbbecManufacturing={mount:second};await assets();
 await select('rollback-v1','rollback');
 expect(first).toHaveBeenCalledTimes(2);expect(second).toHaveBeenCalledTimes(1);
 const styles=Array.from(document.querySelectorAll<HTMLLinkElement>('link[data-manufacturing-asset]'));
 expect(styles.filter(node=>node.media==='all').map(node=>node.href)).toEqual([expect.stringContaining('release=rollback-v1')]);
});
it('ignores stale in-flight scripts and CSS while retaining the selected library',async()=>{
 async function select(release:string){
  vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>({allowed:true,available:true,can_edit:true,release})} as Response);
  await render({...account,csrf_token:release});
 }
 const oldMount=vi.fn(()=>({unmount:vi.fn(),isDirty:()=>false})),newMount=vi.fn(()=>({unmount:vi.fn(),isDirty:()=>false}));
 await select('inflight-old');await select('inflight-new');
 window.OrbbecManufacturing={mount:newMount};
 await act(async()=>document.querySelectorAll('[data-manufacturing-asset*="inflight-new"]').forEach(node=>node.dispatchEvent(new Event('load'))));
 window.OrbbecManufacturing={mount:oldMount};
 await act(async()=>document.querySelectorAll('[data-manufacturing-asset*="inflight-old"]').forEach(node=>node.dispatchEvent(new Event('load'))));
 expect(oldMount).not.toHaveBeenCalled();expect(newMount).toHaveBeenCalledTimes(1);
 const oldStyle=document.querySelector<HTMLLinkElement>('link[data-manufacturing-asset*="inflight-old"]')!;
 expect(oldStyle.media).toBe('not all');
 await act(async()=>root.render(<p>Retired</p>));await select('inflight-new');expect(newMount).toHaveBeenCalledTimes(2);expect(oldMount).not.toHaveBeenCalled();
});
