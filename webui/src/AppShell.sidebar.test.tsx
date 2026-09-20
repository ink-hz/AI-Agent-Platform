/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { AppShell } from './AppShell';
import type { Account } from './auth';
import type { Route } from './router';
const deployment = vi.hoisted(() => vi.fn());
vi.mock('./api', () => ({ fetchDeployment: deployment }));
const member: Account = { internal_user_id:'m', display_name:'苍渊', role:'member', departments:[], gender:null, observation_agent_ids:[], workspace_scopes:[], directory_freshness:'fresh', hard_stale_read_only:false, csrf_token:'c' };
let box: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => {
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
 const stored=new Map<string,string>(); vi.stubGlobal('localStorage',{getItem:(key:string)=>stored.get(key)??null,setItem:(key:string,value:string)=>stored.set(key,value)}); window.history.replaceState({}, '', '/');
 deployment.mockResolvedValue({mode:'local',read_only:false,freshness:'current'});
 box=document.createElement('div');document.body.append(box);root=createRoot(box);
});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();vi.restoreAllMocks();vi.unstubAllGlobals();});
async function render(role: Account['role']='platform_owner',route:Route={name:'home'}) {
 await act(async()=>root.render(<AppShell panorama route={route} account={{...member,role}}><h1>全景内容</h1></AppShell>));
}
const link=(path:string)=>box.querySelector<HTMLAnchorElement>(`.platform-sidebar a[href="${path}"]`);
it('places classified navigation outside main and keeps the topbar to brand and account',async()=>{
 await render();
 expect(box.querySelector('main .platform-sidebar')).toBeNull();
 expect(box.querySelector('.topbar nav')).toBeNull();
 expect(link('/brain')?.textContent).toBe('AI 助手');
 expect(link('/agents')?.textContent).toBe('Agent 目录');
 expect(link('/fae/manage/')?.textContent).toBe('技术支持');
 expect(link('/hr/')?.textContent).toBe('人力资源');
 expect(link('/office/')?.textContent).toBe('行政服务');
 expect(link('/admin/voc')?.textContent).toBe('客户洞察');
 expect(link('/agents')?.closest('section')?.getAttribute('aria-label')).toBe('AI 工作');
 expect(link('/fae/manage/')?.closest('section')?.getAttribute('aria-label')).toBe('业务工作台');
 expect(link('/')?.closest('section')).not.toBe(link('/brain')?.closest('section'));
 expect(link('/admin/activity')?.textContent).toBe('运行事件');
 expect(link('/missions')).not.toBeNull();expect(link('/ai-notes')).not.toBeNull();
 expect(link('/admin/access')).not.toBeNull();
 expect(box.querySelector('.account-chip')?.textContent).toBe('苍渊');
});
it('keeps management and scoped workbench permissions distinct',async()=>{
 await render('member');expect(link('/admin')).toBeNull();expect(link('/admin/identity')).toBeNull();expect(link('/fae/manage/')).toBeNull();expect(link('/fae/')).not.toBeNull();
 await render('management_viewer');expect(link('/admin')).toBeNull();expect(link('/admin/voc')).not.toBeNull();
 await render('platform_admin');expect(link('/admin')).not.toBeNull();expect(link('/admin/access')).toBeNull();expect(link('/fae/manage/')).toBeNull();
 await act(async()=>root.render(<AppShell route={{name:'fae-manage-overview'}} account={{...member,workspace_scopes:['fae_workbench']}}><p>FAE</p></AppShell>));
 expect(link('/fae/manage/')).not.toBeNull();expect(link('/admin')).toBeNull();
});
it('does not invent management access while account is unavailable',async()=>{
 await act(async()=>root.render(<AppShell route={{name:'home'}}><p>等待身份</p></AppShell>));
 expect(link('/admin')).toBeNull();expect(link('/admin/identity')).toBeNull();
});
it('selects the containing destination on deep links without also selecting the overview',async()=>{
 await render('platform_owner',{name:'admin-agent-runtime',agentId:'a'});
 expect(link('/admin/agents')?.getAttribute('aria-current')).toBe('page');
 expect(box.querySelectorAll('.platform-sidebar [aria-current="page"]')).toHaveLength(1);
 await render('platform_owner',{name:'marketing-conversation',agentSlug:'voice',conversationId:'c'});
 expect(link('/agents')?.getAttribute('aria-current')).toBe('page');
});
it('collapses and restores navigation without losing the current content',async()=>{
 await render();const button=box.querySelector<HTMLButtonElement>('[aria-controls="platform-navigation"]')!;
 expect(button).not.toBeNull();expect(button.getAttribute('aria-expanded')).toBe('true');
 await act(async()=>button.click());expect(box.querySelector<HTMLElement>('#platform-navigation')?.hidden).toBe(true);
 expect(box.querySelector('main h1')?.textContent).toBe('全景内容');
 await act(async()=>button.click());expect(box.querySelector<HTMLElement>('#platform-navigation')?.hidden).toBe(false);
});
it('starts narrow screens collapsed even if the desktop preference is expanded',async()=>{
 window.localStorage.setItem('platform.navigation.collapsed','false');
 vi.stubGlobal('matchMedia',vi.fn(()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()})));
 await render();expect(box.querySelector<HTMLElement>('#platform-navigation')?.hidden).toBe(true);
});
it('keeps replica status inside the affected page and review out of a read-only navigation',async()=>{
 deployment.mockResolvedValue({mode:'cloud-replica',read_only:true,freshness:'current',last_success_at:'2026-09-20T09:24:00Z'});
 await render('platform_owner',{name:'admin-overview'});
 expect(link('/admin/review')).toBeNull();
 expect(box.querySelector('main .cloud-replica-banner')?.textContent).toContain('云端副本 · 只读');
 expect(box.querySelector('.platform-sidebar .cloud-replica-banner')).toBeNull();
 await render();expect(box.querySelector('.cloud-replica-banner')).toBeNull();
});

it('keeps review hidden until deployment is known on a direct assistant visit and across routes',async()=>{
 let finish!:(value:unknown)=>void;deployment.mockReturnValue(new Promise(resolve=>{finish=resolve;}));
 const owner={...member,role:'platform_owner' as const};
 await act(async()=>root.render(<AppShell route={{name:'brain'}} account={owner}><p>助手</p></AppShell>));
 expect(deployment).toHaveBeenCalled();expect(link('/admin/review')).toBeNull();
 await act(async()=>finish({mode:'cloud-replica',read_only:true,freshness:'current'}));
 await act(async()=>root.render(<AppShell route={{name:'admin-overview'}} account={owner}><p>概览</p></AppShell>));
 await act(async()=>root.render(<AppShell route={{name:'agents'}} account={owner}><p>目录</p></AppShell>));
 expect(link('/admin/review')).toBeNull();
});
