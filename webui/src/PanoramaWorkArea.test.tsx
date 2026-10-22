/** @vitest-environment jsdom */
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SdkAdminPage } from './sdk-admin/SdkAdminPage';
import type { Account } from './auth';
import type { SdkAdminClient } from './sdk-admin/sdkAdminApi';
import { PanoramaWorkArea } from './PanoramaWorkArea';
function Business() { const [value,setValue]=useState(''); return <input aria-label="业务输入" value={value} onChange={e=>setValue(e.target.value)} />; }
describe('retained panorama workspace', () => {
 const box=document.createElement('div'); document.body.append(box); const root=createRoot(box);
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 afterEach(async()=>{await act(async()=>root.render(null));vi.restoreAllMocks();});
 it('keeps the business component and input mounted when collapsed and reopened', async()=>{
  const render=()=> <Business/>; const props={renderWorkspace:render,onClose:vi.fn(),onDirty:vi.fn()};
  await act(async()=>root.render(<PanoramaWorkArea {...props} route={{name:'brain'}}/>));
  const input=box.querySelector('input')!;
  const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!;
  await act(async()=>{setter.call(input,'保留草稿');input.dispatchEvent(new Event('input',{bubbles:true}));});
  expect(props.onDirty).toHaveBeenCalledWith(true);
  await act(async()=>root.render(<PanoramaWorkArea {...props}/>));
  expect(box.querySelector('input')).toBe(input);
  expect(box.querySelector('section')?.hidden).toBe(true);
  await act(async()=>root.render(<PanoramaWorkArea {...props} route={{name:'brain'}}/>));
  expect(box.querySelector('input')?.value).toBe('保留草稿');
 });
 it('isolates a broken business component from the panorama', async()=>{
  vi.spyOn(console,'error').mockImplementation(()=>{});
  const Broken=()=>{throw Error('business failed');};
  await act(async()=>root.render(<PanoramaWorkArea route={{name:'brain'}} renderWorkspace={()=> <Broken/>} onClose={vi.fn()} onDirty={vi.fn()}/>));
  expect(box.textContent).toContain('工作区暂时不可用');
  expect(box.textContent).toContain('回到全景');
 });
});

it('keeps SDK full-text search and filters out of business draft tracking',async()=>{
 const box=document.createElement('div');document.body.append(box);const root=createRoot(box);
 const onDirty=vi.fn();const account={internal_user_id:'reader'} as Account;
 const client:SdkAdminClient={
  access:vi.fn().mockResolvedValue({allowed:true}),
  catalog:vi.fn().mockResolvedValue({generated_at:'2026-10-22',counts:{knowledge_pages:1},groups:[{id:'01-system',title:'系统架构',count:1}]}),
  documents:vi.fn().mockResolvedValue({items:[],total:0,offset:0,limit:30}),document:vi.fn(),
 };
 try {
  await act(async()=>root.render(<PanoramaWorkArea route={{name:'sdk-admin',section:'knowledge'}} renderWorkspace={()=> <SdkAdminPage section="knowledge" account={account} client={client}/>} onClose={vi.fn()} onDirty={onDirty}/>));
  const input=box.querySelector('input[type="search"]')!;
  await act(async()=>{
   Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'设备');
   input.dispatchEvent(new Event('input',{bubbles:true}));
   box.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  });
  const select=box.querySelector('select')!;
  await act(async()=>{select.value='01-system';select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));});
  expect(client.documents).toHaveBeenLastCalledWith(expect.objectContaining({q:'设备',group:'01-system'}),expect.any(AbortSignal));
  expect(onDirty).not.toHaveBeenCalled();
 }finally{await act(async()=>root.unmount());box.remove();}
});
