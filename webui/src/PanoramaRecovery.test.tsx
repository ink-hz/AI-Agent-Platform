/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { panoramaTestData } from './panoramaTestData';
let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;
let permitted=true;
const panorama=panoramaTestData();
let requests:RequestInit[];
beforeEach(()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 box=document.createElement('div');document.body.append(box);root=createRoot(box);requests=[];permitted=true;
 const meta=document.createElement('meta');meta.name='platform-identity-mode';meta.content='enabled';document.head.append(meta);
 window.history.replaceState({},'', '/');vi.spyOn(window,'scrollTo').mockImplementation(()=>{});
 vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
  const url=String(input);const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}});
  if(url.endsWith('/api/v1/account'))return json({internal_user_id:'admin',display_name:'Admin',role:'platform_admin',departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'});
  if(url.endsWith('/ai-engineering/access'))return json({allowed:permitted});
  if(url.endsWith('/ai-engineering/panorama'))return json(panorama);
  if(url.endsWith('/ai-engineering/organization'))return json({generation_id:'10000000-0000-4000-8000-000000000001',completed_at:'2026-09-21T00:00:00Z',freshness:'fresh',scope:'visible_directory',root_id:'10000000-0000-4000-8000-000000000002',departments:[{id:'10000000-0000-4000-8000-000000000002',parent_id:null,name:'Organization'}]});
  if(url.includes('/api/v1/conversations')){
    if(init?.method==='POST'){requests.push(init);throw new TypeError('response lost after send');}
    return json({items:[],next_cursor:null});
  }
  return json({mode:'local',read_only:false,auth:'dingtalk',freshness:'current',last_success_at:null});
 });
});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();document.querySelector('meta[name="platform-identity-mode"]')?.remove();vi.restoreAllMocks();window.history.replaceState({},'', '/');});
async function openDraft(){
 await act(async()=>root.render(<App/>));
 expect(box.textContent).toContain("测试全景");
 await act(async()=>box.querySelector<HTMLButtonElement>('[data-node-id="digital"] button')!.click());
 await act(async()=>box.querySelector<HTMLButtonElement>('[data-action-id="brain"]')!.click());
 const input=box.querySelector<HTMLTextAreaElement>('#brain-request')!;
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(input,'保留这条请求');input.dispatchEvent(new Event('input',{bubbles:true}));});
 return input;
}
it('protects native links and history; failed submission survives collapse and keeps its idempotency key',async()=>{
 const input=await openDraft();const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 await act(async()=>box.querySelector<HTMLAnchorElement>('.brain-ai-notes-entry')!.click());
 expect(confirm).toHaveBeenCalledOnce();expect(window.location.pathname).toBe('/brain');expect(box.querySelector('#brain-request')).toBe(input);
 await act(async()=>{window.history.replaceState({panorama:true},'', '/ai-notes');window.dispatchEvent(new PopStateEvent('popstate'));});
 expect(window.location.pathname).toBe('/brain');expect(input.value).toBe('保留这条请求');
 await act(async()=>box.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 expect(requests).toHaveLength(1);
 await act(async()=>[...box.querySelectorAll('button')].find(b=>b.textContent==='回到全景')!.click());
 expect(window.location.pathname).toBe('/');
 await act(async()=>box.querySelector<HTMLButtonElement>('[data-action-id="notes"]')!.click());
 expect(window.location.pathname).toBe('/');expect(box.querySelector('#brain-request')).toBe(input);
 await act(async()=>box.querySelector<HTMLButtonElement>('[data-action-id="brain"]')!.click());
 await act(async()=>box.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 expect(requests).toHaveLength(2);expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});
it('revocation on focus removes both the panorama and retained private workspace',async()=>{
 await openDraft();
 await act(async()=>[...box.querySelectorAll('button')].find(b=>b.textContent==='回到全景')!.click());
 expect(box.querySelector('#brain-request')).not.toBeNull();permitted=false;
 await act(async()=>window.dispatchEvent(new Event('focus')));
 expect(box.querySelector('#brain-request')).toBeNull();expect(box.textContent).not.toContain('测试全景');expect(box.textContent).toContain('无权限');expect(box.textContent).toContain('请联系苍渊');
 expect(box.querySelector('.platform-sidebar')).toBeNull();expect(box.querySelector('.topbar')).toBeNull();
 // The denied panorama must not poison independently authorized member pages.
 await act(async()=>{window.history.replaceState({},'', '/brain');window.dispatchEvent(new PopStateEvent('popstate'));});
 expect(box.querySelector('#brain-request')).not.toBeNull();expect(box.textContent).not.toContain('请联系苍渊');
});
it('keeps an unsaved workspace mounted while switching between company canvases',async()=>{
 const input=await openDraft();const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 await act(async()=>box.querySelector<HTMLAnchorElement>('.platform-sidebar a[href="/organization"]')!.click());
 expect(window.location.pathname).toBe('/organization');expect(confirm).not.toHaveBeenCalled();expect(box.querySelector('#brain-request')).toBe(input);expect(input.value).toBe('保留这条请求');
 await act(async()=>box.querySelector<HTMLAnchorElement>('.platform-sidebar a[href="/"]')!.click());
 expect(window.location.pathname).toBe('/');expect(box.querySelector('#brain-request')).toBe(input);
 await act(async()=>box.querySelector<HTMLButtonElement>('[data-action-id="brain"]')!.click());
 expect(window.location.pathname).toBe('/brain');expect(box.querySelector('#brain-request')).toBe(input);expect(input.value).toBe('保留这条请求');
});
