/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PlatformFunctions } from './PlatformFunctions';
import { DeploymentProvider } from '../deploymentContext';
const admin:any={role:'platform_admin',workspace_scopes:[],display_name:'管理员'};
let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;box=document.createElement('div');document.body.append(box);root=createRoot(box);});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();});
async function render(account=admin,cloud=false){
 const action=vi.fn(),path=vi.fn();
 await act(async()=>root.render(<DeploymentProvider resolved deployment={cloud?{mode:'cloud-replica',read_only:true} as any:null}><PlatformFunctions account={account} onAction={action} onOpenPath={path}/></DeploymentProvider>));
 return {action,path};
}
it('exposes existing platform functions without selecting a graph node',async()=>{
 const {action}=await render();
 expect(box.querySelector('a[href="/admin/identity"]')?.textContent).toBe('身份管理');
 expect(box.querySelector('a[href="/admin/agents"]')?.textContent).toBe('Agent 管理');
 expect(box.querySelector('a[href="/admin/access"]')).toBeNull();
 await act(async()=>box.querySelector<HTMLAnchorElement>('a[href="/brain"]')!.click());
 expect(action).toHaveBeenCalledWith('brain');
});
it('retains owner and independent workspace scopes and cloud navigation rules',async()=>{
 await render(admin,true);expect(box.querySelector('a[href="/admin/review"]')).toBeNull();expect(box.querySelector('a[href="/fae/manage/"]')).toBeNull();
 await render({...admin,role:'platform_owner'});
 expect(box.querySelector('a[href="/admin/access"]')).not.toBeNull();expect(box.querySelector('a[href="/fae/manage/"]')).not.toBeNull();
 await render({...admin,workspace_scopes:['fae_workbench']});expect(box.querySelector('a[href="/fae/manage/"]')).not.toBeNull();
});
it('keeps independent applications and management endpoints distinct',async()=>{
 const {action,path}=await render({...admin,role:'platform_owner'});
 for(const prefix of ['hr','office','voc','fae'])expect(box.querySelector(`a[href="/${prefix}/"]`)).not.toBeNull();
 await act(async()=>box.querySelector<HTMLAnchorElement>('a[href="/office/"]')!.click());expect(action).toHaveBeenCalledWith('office');
 await act(async()=>box.querySelector<HTMLAnchorElement>('a[href="/voc/manage/"]')!.click());expect(path).toHaveBeenCalledWith('/voc/manage/',true);
});
it('does not advertise management shortcuts to a non-management identity',async()=>{
 await render({...admin,role:'member'});expect(box.querySelector('nav')).toBeNull();
});
