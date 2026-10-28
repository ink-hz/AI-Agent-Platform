/** @vitest-environment jsdom */
import { manufacturingBootstrap } from '../manufacturingTestFixture';
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
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:200,json:async()=>manufacturingBootstrap({allowed:true,available:true,can_edit:true,release:`test-${++release}`})}));
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
 expect(fetch).toHaveBeenCalledWith('/_preview/dingtalk-r1/api/v1/manufacturing/workflow/access',expect.objectContaining({credentials:'same-origin'}));
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
 vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>manufacturingBootstrap({allowed:true,available:false})} as Response);
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
 await render();await act(async()=>root.render(<p>Retired</p>));await act(async()=>resolve({ok:true,status:200,json:async()=>manufacturingBootstrap({allowed:true,available:true,release:'late'})} as Response));
 expect(mount).not.toHaveBeenCalled();expect(document.querySelector('[data-manufacturing-asset]')).toBeNull();
});
it('uses the library heading when mounted',async()=>{
 mount.mockImplementation((element,opts)=>{options=opts;element.innerHTML='<h1>大制造</h1>';return {unmount,isDirty:()=>false};});
 await render();await assets();expect(box.querySelectorAll('h1')).toHaveLength(1);
});
it('loads each asset URL once across route re-entry',async()=>{
 vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>manufacturingBootstrap({allowed:true,available:true,can_edit:true,release:'shared-release'})} as Response);
 await render();await assets();await act(async()=>root.render(<p>Retired</p>));await render();
 expect(document.querySelectorAll('script[data-manufacturing-asset]')).toHaveLength(1);expect(document.querySelectorAll('link[data-manufacturing-asset]')).toHaveLength(1);
 expect(mount).toHaveBeenCalledTimes(2);
});
it('binds cached script and active CSS to v1→v2→v1 release transitions',async()=>{
 const first=vi.fn(()=>({unmount:vi.fn(),isDirty:()=>false})), second=vi.fn(()=>({unmount:vi.fn(),isDirty:()=>false}));
 async function select(release:string,csrf:string){
  vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>manufacturingBootstrap({allowed:true,available:true,can_edit:true,release})} as Response);
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
  vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:async()=>manufacturingBootstrap({allowed:true,available:true,can_edit:true,release})} as Response);
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

it('injects the shared Markdown renderer with safe GFM and owned cleanup',async()=>{
 await render();await assets();expect(options.renderMarkdown).toEqual(expect.any(Function));
 const element=document.createElement('div');box.querySelector('[data-manufacturing-root]')!.append(element);
 let first:any,second:any;
 await act(async()=>{first=options.renderMarkdown(element,{markdown:'# First\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))',headingIds:{1:'source-first'}});});
 expect(element.querySelector('h1')?.id).toBe('source-first');expect(element.querySelector('table')).not.toBeNull();
 expect(element.querySelector('.article-markdown')).not.toBeNull();expect(element.querySelector('script')).toBeNull();
 expect(element.querySelector('a')?.getAttribute('href')).not.toContain('javascript:');
 await act(async()=>{second=options.renderMarkdown(element,{markdown:'# Second'});first.unmount();});
 expect(element.textContent).toBe('Second');
 await act(async()=>second.unmount());expect(element.childNodes).toHaveLength(0);
 const retired=options.renderMarkdown;
 await act(async()=>root.render(<p>Retired</p>));
 await act(async()=>retired(element,{markdown:'Stale'}));expect(element.childNodes).toHaveLength(0);
});

it('retires nested Markdown roots safely through StrictMode, replacement and auth lifetime',async()=>{
 const errors=vi.spyOn(console,'error');
 let oldRender:any,handle:any;
 mount.mockImplementation((element,opts)=>{
  options=opts;oldRender=opts.renderMarkdown;
  if(opts.renderMarkdown){const child=document.createElement('div');element.append(child);handle=opts.renderMarkdown(child,{markdown:'# Owned'});}
  return {unmount:()=>{handle?.unmount();element.replaceChildren();},isDirty:()=>false};
 });
 await render(account,true);await assets();expect(oldRender).toEqual(expect.any(Function));
 const retired=oldRender;
 await act(async()=>root.render(<p>Retired</p>));
 expect(errors.mock.calls).toEqual([]);
 const detached=document.createElement('div');
 await act(async()=>retired(detached,{markdown:'# Late'}));expect(detached.childNodes).toHaveLength(0);
 await render({...account,csrf_token:'new-lifetime'});await assets();expect(options.renderMarkdown).not.toBe(retired);
 expect(errors.mock.calls).toEqual([]);
});

it.each([
 {csrf_token:'rotated'}, {role:'member' as const},
 {internal_user_id:'other-user'}, {hard_stale_read_only:true},
])('invalidates renderer ownership on account authorization change %o',async(change)=>{
 await render();await assets();const retired=options.renderMarkdown;
 const element=document.createElement('div');box.querySelector('[data-manufacturing-root]')!.append(element);
 await act(async()=>retired(element,{markdown:'# Previous account'}));expect(element.textContent).toBe('Previous account');
 await render({...account,...change});expect(element.childNodes).toHaveLength(0);
 await act(async()=>retired(element,{markdown:'# Stale callback'}));expect(element.childNodes).toHaveLength(0);
 await assets();expect(options.renderMarkdown).not.toBe(retired);
});

it('keeps the renderer stable within a mount and retires it on auth failure',async()=>{
 await render();await assets();const retired=options.renderMarkdown;
 await render({...account,display_name:'Updated display name'});expect(options.renderMarkdown).toBe(retired);expect(mount).toHaveBeenCalledTimes(1);
 const element=document.createElement('div');box.querySelector('[data-manufacturing-root]')!.append(element);
 await act(async()=>retired(element,{markdown:'# Private content'}));
 await act(async()=>options.onAuthError());expect(element.childNodes).toHaveLength(0);
 await act(async()=>retired(element,{markdown:'# Stale content'}));expect(element.childNodes).toHaveLength(0);
});
it('uses current account metadata when assets finish and updates it without remounting',async()=>{
 const updateUser=vi.fn(); mount.mockImplementation((_element,opts)=>{options=opts;return {unmount,isDirty:()=>true,updateUser};});
 await render();await render({...account,display_name:'加载中的新姓名'});await assets();
 expect(options.user).toEqual({displayName:'加载中的新姓名',roleLabel:'平台管理员'});
 await render({...account,display_name:'现用姓名'});
 expect(updateUser).toHaveBeenLastCalledWith({displayName:'现用姓名',roleLabel:'平台管理员'});
 expect(mount).toHaveBeenCalledTimes(1);expect(unmount).not.toHaveBeenCalled();
 await act(async()=>root.render(<p>Retired</p>));
});
it('provides a guarded live return callback that retires with its owner',async()=>{
 await render();await assets();
 expect(typeof options.onNavigatePlatform).toBe('function');
 await act(async()=>options.onNavigatePlatform());expect(window.location.pathname).toBe('/');
 await act(async()=>root.render(<p>Retired</p>));window.history.replaceState({},'','/manufacturing');
 options.onNavigatePlatform();expect(window.location.pathname).toBe('/manufacturing');
});
it('keeps the directory safety read-only explanation visible in standalone mode',async()=>{
 await render({...account,hard_stale_read_only:true});await assets();
 expect(box.textContent).toContain('通讯录已超过安全时限');expect(options.readOnly).toBe(true);
});
it('logs out with the current CSRF once, redirects only on success, and retires pending redirects',async()=>{
 await render();await assets();
 const realWindow=window, replace=vi.fn();
 vi.stubGlobal('window',new Proxy(realWindow,{get(target,key){return key==='location'?{...target.location,replace}:Reflect.get(target,key);}}));
 let finish!:(response:Response)=>void;
 vi.mocked(fetch).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 const first=options.onLogout();await options.onLogout();
 expect(fetch).toHaveBeenLastCalledWith('/api/v1/auth/logout',expect.objectContaining({method:'POST',headers:expect.objectContaining({'X-CSRF-Token':'trusted-csrf'})}));
 const count=vi.mocked(fetch).mock.calls.length;expect(replace).not.toHaveBeenCalled();
 finish(new Response('{}',{status:500}));await expect(first).rejects.toThrow();expect(replace).not.toHaveBeenCalled();
 const success=options.onLogout();finish(new Response('{}'));await success;
 expect(fetch).toHaveBeenCalledTimes(count+1);expect(replace).toHaveBeenCalledWith('/login');
 replace.mockClear();const pending=options.onLogout();await act(async()=>root.render(<p>Retired</p>));
 finish(new Response('{}'));await pending;expect(replace).not.toHaveBeenCalled();
 await options.onLogout();expect(fetch).toHaveBeenCalledTimes(count+2);
});
