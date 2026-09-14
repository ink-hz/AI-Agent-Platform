/** @vitest-environment jsdom */
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {beforeEach,afterEach,expect,it,vi} from 'vitest';
import type {Account} from '../../auth';
const account={internal_user_id:'owner',csrf_token:'csrf',display_name:'HR',hard_stale_read_only:false} as Account;
let host:HTMLDivElement,root:ReturnType<typeof createRoot>;
beforeEach(()=>{host=document.createElement('div');document.body.append(host);root=createRoot(host);(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;history.replaceState({},'', '/hr/panorama');});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();vi.clearAllMocks();});
import {HrPanoramaWorkspace} from './HrPanoramaWorkspace';
vi.mock('./HrSourceWorkspace',()=>({HrSourceWorkspace:()=> <p>Current sources</p>}));
vi.mock('./HrResearchWorkspace',()=>({HrResearchWorkspace:()=> <p>Current research</p>}));
it.each(['?view=archive','?view=topics','?company=old','?topic=old','?bundle_id=old'])('keeps current readers for retired archive parameters %s',async query=>{
 history.replaceState({},'', '/hr/panorama'+query);
 await act(async()=>root.render(<HrPanoramaWorkspace account={account}/>));
 expect(host.textContent).toContain('Current sources');
 expect(host.textContent).not.toContain('历史情报归档');
 await act(async()=>Array.from(host.querySelectorAll('button')).find(b=>b.textContent==='AI 分析报告')!.click());
 expect(host.querySelector('[data-layer="research"]')?.hasAttribute('hidden')).toBe(false);
 expect(host.textContent).toContain('Current research');
});
