/** @vitest-environment jsdom */
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
