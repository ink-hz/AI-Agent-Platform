/** @vitest-environment jsdom */
import { act } from 'react';
import { AppShell } from '../AppShell';
import * as brainApi from '../brainApi';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { WebCatalogProvider } from './WebCatalog';
import { AgentUseDirectoryPage } from '../pages/AgentUseDirectoryPage';
import { PlatformSidebar } from './PlatformSidebar';
import { PanoramaView } from '../panorama/PanoramaView';
import { panoramaTestData } from '../panoramaTestData';
import { fetchAgentCatalog } from '../brainApi';
import type { Account } from '../auth';
import type { AgentCapabilityCard } from '../brainTypes';

const card: AgentCapabilityCard = {
 agent_id:'hr-bot',display_name:'HR',persona_subtitle:null,domain_group:'HR',mission:'招聘服务',capabilities:['招聘'],exclusions:[],example_tasks:[],required_inputs:[],accepted_input_types:['text'],output_types:['text'],supports_attachments_in:false,supports_attachments_out:false,attachment_limits:null,supports_evidence:true,supports_streaming:true,supports_cancellation:true,supports_idempotency:true,max_duration_seconds:300,data_classification:'internal',adapter_id:'legacy',adapter_kind:'metabot_local',adapter_config_version:1,output_contract:'normalized_task_result_v1',interaction_modes:['direct_chat'],workspace_url:null,capability_version:1,web_entry:{kind:'independent_service',path:'/hr/'},
};
const account:Account={internal_user_id:'owner',display_name:'Owner',role:'platform_owner',departments:[],gender:null,workspace_scopes:[],observation_agent_ids:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'};
let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;box=document.createElement('div');document.body.append(box);root=createRoot(box);vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('unavailable')));});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();vi.restoreAllMocks();vi.unstubAllGlobals();});
it('shares exactly one authenticated projection across directory, sidebar and contextual home actions',async()=>{
 const load=vi.fn().mockResolvedValue([card]);const data=panoramaTestData();data.nodes.find(node=>node.id==='digital')!.actions=['brain','missions','notes','identity','hr','fae'];
 await act(async()=>root.render(<WebCatalogProvider identityKey="owner" loadCatalog={load}><PlatformSidebar route={{name:'home'}} account={account} readOnly={false} collapsed={false}/><AgentUseDirectoryPage/><PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()}/></WebCatalogProvider>));
 await act(async()=>box.querySelector<HTMLButtonElement>('[data-node-id="digital"] button')!.click());
 expect(load).toHaveBeenCalledTimes(1);
 expect(box.querySelector('.agent-use-card')?.getAttribute('href')).toBe('/hr/');
 expect(box.querySelector('.platform-sidebar a[href="/hr/"]')).not.toBeNull();
 expect(box.querySelector('[data-action-id="hr"]')?.getAttribute('href')).toBe('/hr/');
 expect(box.querySelectorAll('[data-action-id]')).toHaveLength(1);
 const anchor=box.querySelector<HTMLAnchorElement>('[data-action-id="hr"]')!;
 anchor.addEventListener('click',event=>{expect(event.defaultPrevented).toBe(false);event.preventDefault();},{once:true});
 await act(async()=>anchor.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,metaKey:true})));
});
it('aborts and discards late old-identity results while clearing previous grants synchronously',async()=>{
 let oldDone!:(cards:AgentCapabilityCard[])=>void;let newDone!:(cards:AgentCapabilityCard[])=>void;
 const oldLoad=vi.fn().mockImplementation(()=>new Promise(resolve=>{oldDone=resolve;}));const newLoad=vi.fn().mockImplementation(()=>new Promise(resolve=>{newDone=resolve;}));
 const render=(key:string,load:typeof oldLoad)=><WebCatalogProvider identityKey={key} loadCatalog={load}><AgentUseDirectoryPage/></WebCatalogProvider>;
 await act(async()=>root.render(render('old',oldLoad)));
 await act(async()=>root.render(render('new',newLoad)));
 expect(oldLoad.mock.calls[0][0].aborted).toBe(true);
 await act(async()=>oldDone([card]));expect(box.querySelector('a[href="/hr/"]')).toBeNull();
 await act(async()=>newDone([{...card,agent_id:'voc',web_entry:{kind:'independent_service',path:'/voc/'}}]));
 expect(box.querySelector('a[href="/voc/"]')).not.toBeNull();
 await act(async()=>root.render(render('third',oldLoad)));expect(box.querySelector('.agent-use-card')).toBeNull();
 await act(async()=>root.unmount());expect(oldLoad.mock.calls[1][0].aborted).toBe(true);root=createRoot(box);
});
it('distinguishes fetch failure, retry and empty authorization without restoring hardcoded launches',async()=>{
 const load=vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue([]);
 await act(async()=>root.render(<WebCatalogProvider identityKey="owner" loadCatalog={load}><AgentUseDirectoryPage/><PlatformSidebar route={{name:'home'}} account={account} readOnly={false} collapsed={false}/></WebCatalogProvider>));
 expect(box.querySelector('.agent-use-card')).toBeNull();expect(box.textContent).not.toContain('暂时没有可用的专业 Agent');
 expect(box.querySelector('a[href="/fae/manage/"]')).not.toBeNull();
 await act(async()=>Array.from(box.querySelectorAll('button')).find(button=>button.textContent?.includes('重试'))!.click());
 expect(box.textContent).toContain('暂时没有可用的专业 Agent');expect(load).toHaveBeenCalledTimes(2);
});
it('keeps unranked published Web applications instead of using presentation order as admission',async()=>{
 await act(async()=>root.render(<AgentUseDirectoryPage loadCatalog={vi.fn().mockResolvedValue([{...card,agent_id:'new-web-service',display_name:'新服务'}])}/>));
 expect(box.querySelector('a[href="/hr/"]')?.textContent).toContain('新服务');
});
it('never requests a catalog for an absent identity',async()=>{
 const load=vi.fn();await act(async()=>root.render(<WebCatalogProvider identityKey={null} loadCatalog={load}><AgentUseDirectoryPage/></WebCatalogProvider>));expect(load).not.toHaveBeenCalled();expect(box.querySelector('.agent-use-card')).toBeNull();
});
it.each(['/hr/','/daq/','/office/?view=services'])('parses Web metadata independently of legacy runtime fields: %s',async path=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({agents:[{...card,web_entry:{kind:'independent_service',path}}]}))));
 await expect(fetchAgentCatalog()).resolves.toMatchObject([{adapter_kind:'metabot_local',workspace_url:null,web_entry:{path}}]);
 expect(fetch).toHaveBeenCalledWith('/api/v1/catalog/agents',expect.objectContaining({credentials:'include'}));
});
it.each([{}, {kind:'other',path:'/hr/'}, {kind:'independent_service',path:'/hr/?x=1'}, {kind:'independent_service',path:'/hr/\n'}, {kind:'independent_service',path:'/%68r/'}, {kind:'independent_service',path:'https://example.com/hr/'}])('rejects explicit malformed Web metadata in the API parser: %j',async web_entry=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({agents:[{...card,web_entry}]}))));await expect(fetchAgentCatalog()).rejects.toThrow('Agent catalog response invalid');
});

it('binds AppShell catalog sessions to the actual account and ignores a late previous-account response',async()=>{
 let oldDone!:(cards:AgentCapabilityCard[])=>void;let newDone!:(cards:AgentCapabilityCard[])=>void;
 const load=vi.spyOn(brainApi,'fetchManagementAgentCatalog').mockImplementationOnce(()=>new Promise(resolve=>{oldDone=resolve;})).mockImplementationOnce(()=>new Promise(resolve=>{newDone=resolve;}));
 const render=(id:string)=><AppShell route={{name:'agents'}} account={{...account,internal_user_id:id}}><AgentUseDirectoryPage/></AppShell>;
 await act(async()=>root.render(render('old-account')));
 await act(async()=>root.render(render('new-account')));
 expect(load.mock.calls[0][0]?.aborted).toBe(true);
 await act(async()=>oldDone([card]));expect(box.querySelector('.agent-use-card')).toBeNull();expect(box.querySelector('.platform-sidebar a[href="/hr/"]')).toBeNull();
 await act(async()=>newDone([]));expect(box.textContent).toContain('暂时没有可用的专业 Agent');expect(load).toHaveBeenCalledTimes(2);
});

it('keeps full de-duplicated inventory while filtering use links and giving HR Web precedence',async()=>{
 const cards=[{...card,web_entry:null}, {...card,can_use:false}, ...Array.from({length:5},(_,i)=>({...card,agent_id:`marketing-${i}`,display_name:`渠道${i}`,web_entry:null,can_use:false}))];
 const load=vi.fn().mockResolvedValue(cards);
 await act(async()=>root.render(<WebCatalogProvider identityKey="owner" loadCatalog={load}><AgentUseDirectoryPage/><PlatformSidebar route={{name:'home'}} account={account} readOnly={false} collapsed={false}/></WebCatalogProvider>));
 expect(box.querySelectorAll('.agent-use-card')).toHaveLength(6);expect(box.querySelectorAll('[data-access-method="channel"] .agent-use-card')).toHaveLength(5);expect(box.querySelector('[data-access-method="web"]')?.textContent).toContain('HR');expect(box.textContent).toContain('已配置 6 个 Agent');expect(box.querySelector('.agent-use-directory a')).toBeNull();expect(box.querySelector('.platform-sidebar a[href="/hr/"]')).toBeNull();expect(box.textContent).not.toContain('停用');
});
it('updates revoked use grants without removing management metadata',async()=>{
 const load=vi.fn().mockResolvedValueOnce([{...card,can_use:true}]).mockResolvedValueOnce([{...card,can_use:false}]);
 const render=(key:string)=><WebCatalogProvider identityKey={key} loadCatalog={load}><AgentUseDirectoryPage/></WebCatalogProvider>;
 await act(async()=>root.render(render('before')));expect(box.querySelector('a[href="/hr/"]')).not.toBeNull();await act(async()=>root.render(render('revoked')));expect(box.querySelector('a[href="/hr/"]')).toBeNull();expect(box.querySelectorAll('.agent-use-card')).toHaveLength(1);
});

it.each(['platform_owner','platform_admin','member'] as const)('selects the correct catalog contract for %s without permission fallback',async role=>{
 const managed=vi.spyOn(brainApi,'fetchManagementAgentCatalog').mockResolvedValue([{...card,can_use:false}]);
 const member=vi.spyOn(brainApi,'fetchAgentCatalog').mockResolvedValue([card]);
 await act(async()=>root.render(<AppShell route={{name:'agents'}} account={{...account,role}}><AgentUseDirectoryPage/></AppShell>));
 const manager=role!=='member';expect(managed).toHaveBeenCalledTimes(manager?1:0);expect(member).toHaveBeenCalledTimes(manager?0:1);
 expect(box.querySelector('.agent-use-directory a[href="/hr/"]')!==null).toBe(!manager);
});
it('never falls back to the member catalog or launchers after management catalog failure',async()=>{
 const managed=vi.spyOn(brainApi,'fetchManagementAgentCatalog').mockRejectedValue(new Error('403'));
 const member=vi.spyOn(brainApi,'fetchAgentCatalog').mockResolvedValue([card]);
 await act(async()=>root.render(<AppShell route={{name:'agents'}} account={account}><AgentUseDirectoryPage/></AppShell>));
 expect(managed).toHaveBeenCalledTimes(1);expect(member).not.toHaveBeenCalled();expect(box.querySelector('.agent-use-directory a')).toBeNull();expect(box.querySelector('.agent-use-directory')?.textContent).toContain('重试');
});
it.each([undefined,'true',null])('requires an explicit boolean can_use on management metadata: %j',async can_use=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({agents:[{...card,can_use}]}))));await expect(brainApi.fetchManagementAgentCatalog()).rejects.toThrow('Agent catalog response invalid');
});
it('preserves complete management metadata and false use permission through the HTTP adapter',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({agents:[{...card,can_use:false},{...card,agent_id:'channel',web_entry:null,can_use:false}]}))));
 await expect(brainApi.fetchManagementAgentCatalog()).resolves.toMatchObject([{agent_id:'hr-bot',can_use:false},{agent_id:'channel',can_use:false}]);
 expect(fetch).toHaveBeenCalledWith('/api/dashboard/catalog',expect.objectContaining({credentials:'include',cache:'no-store'}));
});
