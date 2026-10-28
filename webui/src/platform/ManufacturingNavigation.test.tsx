/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect,it,vi } from 'vitest';
import { PlatformSidebar } from './PlatformSidebar';
import type { Account } from '../auth';
vi.mock('../sdk-admin/sdkAdminApi',()=>({sdkAdminClient:{access:async()=>({allowed:false})}}));
it('shows manufacturing for any authorized identity and clears when unavailable',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 const fetcher=vi.fn().mockResolvedValue({ok:true,status:200,json:async()=>({allowed:true,available:true})});vi.stubGlobal('fetch',fetcher);
 const account:Account={internal_user_id:'admin',display_name:'Admin',role:'platform_admin',departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'};
 const box=document.createElement('div');document.body.append(box);const root=createRoot(box);
 try {
  await act(async()=>root.render(<PlatformSidebar route={{name:'manufacturing'}} account={account} readOnly={false} collapsed={false}/>));
  expect(box.querySelector('a[href="/manufacturing"]')?.textContent).toBe('大制造');
  expect(box.querySelector('a[href="/manufacturing"]')?.getAttribute('aria-current')).toBe('page');
  await act(async()=>root.render(<PlatformSidebar route={{name:'home'}} account={{...account,role:'member'}} readOnly={false} collapsed={false}/>));
  expect(box.querySelector('a[href="/manufacturing"]')?.textContent).toBe('大制造');
  fetcher.mockResolvedValue({ok:true,status:200,json:async()=>({allowed:true,available:false})});
  await act(async()=>root.render(<PlatformSidebar route={{name:'home'}} account={account} readOnly={false} collapsed={false}/>));
  expect(box.querySelector('a[href="/manufacturing"]')).toBeNull();
 } finally {await act(async()=>root.unmount());box.remove();vi.unstubAllGlobals();}
});
