/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { AppShell } from './AppShell';
import { useDeploymentContext } from './deploymentContext';
const deployment=vi.hoisted(()=>vi.fn());
vi.mock('./api',()=>({fetchDeployment:deployment}));
it('loads original read-only deployment context on the panorama and retains it when a workspace closes',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 const box=document.createElement('div');document.body.append(box);const root=createRoot(box);
 let finish!:(value:any)=>void; deployment.mockReturnValue(new Promise(resolve=>{finish=resolve;}));
 const account:any={internal_user_id:'admin',role:'platform_admin',workspace_scopes:[],hard_stale_read_only:true};
 function Reader(){const {deployment,resolved}=useDeploymentContext();return <p>{!resolved?'权限环境待确认':deployment?.read_only?'只读':'非只读'}</p>;}
 try {
  await act(async()=>root.render(<AppShell panorama route={{name:'home'}} account={account}><Reader/></AppShell>));
  expect(box.querySelector('.topbar')?.parentElement).toBe(box.querySelector('.app'));expect(box.querySelector('main .topbar')).toBeNull();expect(box.textContent).toContain('权限环境待确认');expect(box.querySelector('.topbar')).not.toBeNull();expect(box.textContent).toContain('变更功能已暂停');
  await act(async()=>finish({mode:'cloud-replica',read_only:true,freshness:'current'}));
  await act(async()=>root.render(<AppShell panorama route={{name:'admin-review'}} account={account}><Reader/></AppShell>));
  expect(box.textContent).toContain('只读');expect(box.querySelector('.admin-nav')).not.toBeNull();
  await act(async()=>root.render(<AppShell panorama route={{name:'home'}} account={account}><Reader/></AppShell>));
  expect(box.textContent).toContain('只读');expect(deployment).toHaveBeenCalledTimes(1);
 }finally{await act(async()=>root.unmount());box.remove();}
});
