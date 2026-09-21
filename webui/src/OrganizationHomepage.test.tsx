/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { panoramaTestData } from './panoramaTestData';
const rootId='10000000-0000-4000-8000-000000000001';
const deptId='10000000-0000-4000-8000-000000000002';
const generation='10000000-0000-4000-8000-000000000003';
let box:HTMLDivElement, root:ReturnType<typeof createRoot>, permitted:boolean, detailRequests:number;
beforeEach(()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 permitted=true;detailRequests=0;box=document.createElement('div');document.body.append(box);root=createRoot(box);
 const meta=document.createElement('meta');meta.name='platform-identity-mode';meta.content='enabled';document.head.append(meta);
 window.history.replaceState({},'', '/');vi.spyOn(window,'scrollTo').mockImplementation(()=>{});
 vi.spyOn(globalThis,'fetch').mockImplementation(async(input)=>{
  const u=new URL(String(input),'https://platform.test');const json=(value:unknown)=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
  if(u.pathname.endsWith('/api/v1/account'))return json({internal_user_id:'admin',display_name:'管理员',role:'platform_admin',departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'});
  if(u.pathname.endsWith('/ai-engineering/access'))return json({allowed:permitted});
  if(u.pathname.endsWith('/ai-engineering/panorama'))return json(panoramaTestData());
  if(u.pathname.endsWith('/ai-engineering/organization'))return json({generation_id:generation,completed_at:'2026-09-21T00:00:00Z',freshness:'fresh',scope:'visible_directory',root_id:rootId,departments:[{id:rootId,parent_id:null,name:'Organization'},{id:deptId,parent_id:rootId,name:'组织测试部门'}]});
  if(u.pathname.includes('/organization/departments/')){detailRequests++;return json({generation_id:generation,department_id:deptId,direct_count:1,total_count:1,status_counts:{active:1,inactive:0,disabled:0},position_available:false,members:[{id:'10000000-0000-4000-8000-000000000004',name:'成员示例甲',status:'active',departments:[{id:deptId,name:'组织测试部门'}]}],next_cursor:null});}
  return json({mode:'local',read_only:false,auth:'dingtalk',freshness:'current',last_success_at:null});
 });
});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();document.querySelector('meta[name="platform-identity-mode"]')?.remove();vi.restoreAllMocks();window.history.replaceState({},'', '/');});
it('integrates authenticated structure, lazy member details and whole-home revocation',async()=>{
 await act(async()=>root.render(<App/>));
 expect(box.textContent).toContain('组织测试部门');expect(box.textContent).not.toContain('成员示例甲');expect(detailRequests).toBe(0);
 const department=[...box.querySelectorAll('button')].find(b=>b.textContent?.trim()==='组织测试部门');expect(department).toBeDefined();
 await act(async()=>department!.click());expect(detailRequests).toBe(1);expect(box.textContent).toContain('成员示例甲');expect(box.textContent).toContain('职位尚未同步');
 permitted=false;await act(async()=>window.dispatchEvent(new Event('focus')));
 expect(box.textContent).toContain('请联系苍渊');expect(box.textContent).not.toContain('成员示例甲');expect(box.textContent).not.toContain('组织测试部门');expect(box.querySelector('.platform-sidebar')).toBeNull();
});
