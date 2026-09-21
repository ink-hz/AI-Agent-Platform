/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { panoramaTestData } from './panoramaTestData';

const rootId='10000000-0000-4000-8000-000000000001';
const deptId='10000000-0000-4000-8000-000000000002';
const generation='10000000-0000-4000-8000-000000000003';
const teamId='10000000-0000-4000-8000-000000000005';
let box:HTMLDivElement, root:ReturnType<typeof createRoot>, permitted:boolean, role:'member'|'platform_admin';
let panoramaRequests:number, organizationRequests:number, detailRequests:number;

beforeEach(()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 permitted=true;role='platform_admin';panoramaRequests=0;organizationRequests=0;detailRequests=0;
 box=document.createElement('div');document.body.append(box);root=createRoot(box);
 const meta=document.createElement('meta');meta.name='platform-identity-mode';meta.content='enabled';document.head.append(meta);
 window.history.replaceState({},'', '/');vi.spyOn(window,'scrollTo').mockImplementation(()=>{});
 vi.spyOn(globalThis,'fetch').mockImplementation(async(input)=>{
  const u=new URL(String(input),'https://platform.test');const json=(value:unknown)=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
  if(u.pathname.endsWith('/api/v1/account'))return json({internal_user_id:'admin',display_name:'管理员',role,departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'});
  if(u.pathname.endsWith('/ai-engineering/access'))return json({allowed:permitted});
  if(u.pathname.endsWith('/ai-engineering/panorama')){panoramaRequests++;return json(panoramaTestData());}
  if(u.pathname.endsWith('/ai-engineering/organization')){organizationRequests++;return json({generation_id:generation,completed_at:'2026-09-21T00:00:00Z',freshness:'fresh',scope:'visible_directory',root_id:rootId,departments:[{id:rootId,parent_id:null,name:'Organization'},{id:deptId,parent_id:rootId,name:'组织测试部门'},{id:teamId,parent_id:deptId,name:'组织测试小组'}]});}
  if(u.pathname.includes('/organization/departments/')){detailRequests++;return json({generation_id:generation,department_id:deptId,direct_count:1,total_count:1,status_counts:{active:1,inactive:0,disabled:0},position_available:false,members:[{id:'10000000-0000-4000-8000-000000000004',name:'成员示例甲',status:'active',departments:[{id:deptId,name:'组织测试部门'}]}],next_cursor:null});}
  return json({mode:'local',read_only:false,auth:'dingtalk',freshness:'current',last_success_at:null});
 });
});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();document.querySelector('meta[name="platform-identity-mode"]')?.remove();vi.restoreAllMocks();window.history.replaceState({},'', '/');});

async function render(){await act(async()=>root.render(<App/>));}
async function follow(path:string){const anchor=box.querySelector<HTMLAnchorElement>(`.platform-sidebar a[href="${path}"]`)!;expect(anchor).not.toBeNull();await act(async()=>anchor.click());}
function scroller(view:'business'|'organization'){return box.querySelector<HTMLElement>(`[data-panorama-view="${view}"]`)!;}
async function moveHistory(delta:number){await act(async()=>{const moved=new Promise<void>(resolve=>window.addEventListener('popstate',()=>resolve(),{once:true}));window.history.go(delta);await moved;});}

it('loads each canvas only when first visited and keeps business, organization and scroll state while switching',async()=>{
 await render();
 expect(panoramaRequests).toBe(1);expect(organizationRequests).toBe(0);
 expect(scroller('business').hidden).toBe(false);expect(scroller('organization').hidden).toBe(true);
 const search=box.querySelector<HTMLInputElement>('.panorama-search input')!;
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(search,'数字化');search.dispatchEvent(new Event('input',{bubbles:true}));});
 await act(async()=>box.querySelector<HTMLButtonElement>('[data-node-id="company"] button')!.click());
 scroller('business').scrollTop=320;scroller('business').dispatchEvent(new Event('scroll'));

 await follow('/organization');
 expect(panoramaRequests).toBe(1);expect(organizationRequests).toBe(1);
 expect(scroller('business').hidden).toBe(true);expect(scroller('organization').hidden).toBe(false);
 const expand=box.querySelector<HTMLButtonElement>('[aria-label="展开组织测试部门"]')!;
 await act(async()=>expand.click());expect(expand.getAttribute('aria-expanded')).toBe('true');
 scroller('organization').scrollTop=180;scroller('organization').dispatchEvent(new Event('scroll'));

 await follow('/');
 expect(search.value).toBe('数字化');expect(box.querySelector('[aria-label="company详情"]')).not.toBeNull();
 expect(scroller('business').scrollTop).toBe(320);expect(panoramaRequests).toBe(1);
 await follow('/organization');
 expect(box.querySelector('[aria-label="收起组织测试部门"]')).not.toBeNull();
 expect(scroller('organization').scrollTop).toBe(180);expect(organizationRequests).toBe(2);
});

it('opens the organization deep link without requesting business panorama data',async()=>{
 window.history.replaceState({},'', '/organization');
 await render();
 expect(organizationRequests).toBe(1);expect(panoramaRequests).toBe(0);
 expect(scroller('organization').hidden).toBe(false);expect(box.textContent).toContain('组织测试部门');
});

it('restores each canvas scroll position through browser back and forward',async()=>{
 await render();scroller('business').scrollTop=140;scroller('business').dispatchEvent(new Event('scroll'));
 await follow('/organization');scroller('organization').scrollTop=260;scroller('organization').dispatchEvent(new Event('scroll'));
 await moveHistory(-1);
 expect(window.location.pathname).toBe('/');expect(scroller('business').scrollTop).toBe(140);
 await moveHistory(1);
 expect(window.location.pathname).toBe('/organization');expect(scroller('organization').scrollTop).toBe(260);
});

it('rejects non-administrators before either protected canvas is requested',async()=>{
 role='member';window.history.replaceState({},'', '/organization');
 await render();
 expect(box.textContent).toContain('请联系苍渊');expect(box.querySelector('.platform-sidebar')).toBeNull();
 expect(organizationRequests).toBe(0);expect(panoramaRequests).toBe(0);
});

it('clears the whole retained session when access is revoked from the organization view',async()=>{
 window.history.replaceState({},'', '/organization');await render();
 const department=[...box.querySelectorAll('button')].find(button=>button.textContent?.trim()==='组织测试部门')!;
 await act(async()=>department.click());expect(detailRequests).toBe(1);expect(box.textContent).toContain('成员示例甲');
 permitted=false;await act(async()=>window.dispatchEvent(new Event('focus')));
 expect(box.textContent).toContain('请联系苍渊');expect(box.textContent).not.toContain('成员示例甲');expect(box.textContent).not.toContain('组织测试部门');expect(box.querySelector('.platform-sidebar')).toBeNull();
});
