/** @vitest-environment jsdom */
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {beforeEach,afterEach,expect,it,vi} from 'vitest';
import type {Account} from '../../auth';
const account={internal_user_id:'owner',csrf_token:'csrf',display_name:'HR',hard_stale_read_only:false} as Account;
let host:HTMLDivElement,root:ReturnType<typeof createRoot>;
beforeEach(()=>{vi.spyOn(window,'scrollTo').mockImplementation(()=>{});host=document.createElement('div');document.body.append(host);root=createRoot(host);(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;history.replaceState({},'', '/hr/panorama');});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();vi.clearAllMocks();});
import {HrWorkspacePage} from './HrWorkspacePage';
import {takeHrWorkDraft} from './hrCloudLaunch';
vi.mock('./HrPositionIndex',()=>({HrPositionIndex:({onSelect}:any)=><button onClick={()=>onSelect({positionId:'position-a'})}>Current positions</button>}));
vi.mock('./HrPositionWorkflow',()=>({HrPositionWorkflow:({onDraft,positionId,section}:any)=><button onClick={()=>onDraft({text:'unsent requirement'},positionId)}>Current workflow {section}</button>}));
vi.mock('./HrPanoramaWorkspace',()=>({HrPanoramaWorkspace:()=> <p>Current intelligence</p>}));
it('keeps positions as their own page and explicitly launches current work',async()=>{
 await act(async()=>root.render(<HrWorkspacePage account={account} positions/>));
 expect(host.textContent).toContain('Current positions');
 await act(async()=>host.querySelector('button')!.click());
 expect(takeHrWorkDraft('owner','position-a')).toEqual({text:'',notice:''});
});
it('retains ordinary workflow draft text without sending',async()=>{
 await act(async()=>root.render(<HrWorkspacePage account={account} positionId="position-a" section="context"/>));
 expect(host.textContent).toContain('Current workflow context');
 await act(async()=>host.querySelector('button')!.click());
 expect(takeHrWorkDraft('owner','position-a')).toEqual({text:'unsent requirement',notice:''});
});
it('hosts current intelligence and only three navigation destinations',async()=>{
 await act(async()=>root.render(<HrWorkspacePage account={account} panorama/>));
 expect(host.textContent).toContain('Current intelligence');
 expect(host.querySelectorAll('.hr-workspace-nav a')).toHaveLength(3);
 expect(host.querySelector('.hr-workspace-nav button')).toBeNull();
});
