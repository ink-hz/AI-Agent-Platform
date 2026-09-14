/** @vitest-environment jsdom */
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {it,expect,vi} from 'vitest';
import {HrPositionWorkflow,positionResultStage} from './HrPositionWorkflow';
import type {Account} from '../../auth';
import type {HrApi} from '../../hrApi';
import type {HrR12Api} from '../../hrR12Api';
const account={csrf_token:'csrf-a',hard_stale_read_only:false,internal_user_id:'owner-a'} as Account;
const position={positionId:'position-a',title:'DQE 工程师',locations:['深圳'],department:'研发',internalStatus:'active',sourceVersion:null};
const exact={kind:'result',id:'cloud-result',revision:'result-revision',sha256:'a'.repeat(64)};
async function settle(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});}

it('maps every supported result kind to exactly one of the five stages',()=>{
 expect(['role_calibration','jd','requirements','standard_proposal'].map(positionResultStage)).toEqual(['requirements','requirements','requirements','requirements']);
 expect(positionResultStage('sourcing')).toBe('sourcing');
 expect(positionResultStage('candidate_assessment')).toBe('candidates');
 expect(['interview_plan','interview_record'].map(positionResultStage)).toEqual(['interviews','interviews']);
 expect(positionResultStage('retrospective')).toBe('review');
 expect(positionResultStage('research')).toBeNull();
});

it('uses cloud current and paginated results without reading or rendering legacy data',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);
 const api={position:vi.fn().mockResolvedValue(position)} as unknown as HrApi;
 const r12={context:vi.fn(),positionCandidates:vi.fn(),officialVersions:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);
  if(url.includes('/api/v1/hr/positions/'))throw new Error('legacy results must not be requested');
  if(url.includes('/standards/current'))return new Response(JSON.stringify({ref:{kind:'standard',id:'position-a',revision:'standard-uuid',sha256:'c'.repeat(64)},position_id:'position-a',items:[{item_id:'must',text:'云端当前标准'}],selected_change_ids:['must'],confirmed_at:'2026-09-14T02:00:00Z'}));
  if(url.includes('/results?')&&url.includes('cursor=next'))return new Response(JSON.stringify({items:[{ref:exact,title:'云成果',description:''}],next_cursor:null}));
  if(url.includes('/results?'))return new Response(JSON.stringify({items:[],next_cursor:'next'}));
  if(url.includes('/results/cloud-result/revisions/result-revision'))return new Response(JSON.stringify({ref:exact,kind:'sourcing',title:'云成果',body:'云端成果正文',objects:[],changes:[],base_standard_ref:null,basis:[]}));
  return new Response('{}',{status:404});
 });
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));await settle();
  expect(host.textContent).toContain('已有确认标准');expect(host.textContent).toContain('云成果');
  await act(async()=>[...host.querySelectorAll('nav button')].find(b=>b.textContent?.includes('JD / JR'))!.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  expect(host.textContent).toContain('云端当前标准');expect(host.textContent).not.toContain('standard-uuid');
  expect(host.textContent).not.toContain('旧版标准');expect(host.textContent).not.toContain('旧成果');expect(r12.context).not.toHaveBeenCalled();expect(r12.positionCandidates).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.some(([u])=>String(u).includes('cursor=next'))).toBe(true);
 }finally{await act(async()=>root.unmount());fetcher.mockRestore();}
});

it('opens and downloads the exact cloud result ref even when its saved objects lack the linked position',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);
 const api={position:vi.fn().mockResolvedValue(position)} as unknown as HrApi;
 const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);
  if(url.includes('/api/v1/hr/positions/'))return new Response(JSON.stringify({positionId:'position-a',items:[],nextOffset:null}));
  if(url.includes('/standards/current'))return new Response(JSON.stringify({code:'not_found'}),{status:404});
  if(url.includes('/results?'))return new Response(JSON.stringify({items:[{ref:exact,title:'后来关联成果',description:''}],next_cursor:null}));
  if(url.endsWith('/file'))return new Response('downloaded markdown');
  if(url.includes('/results/cloud-result/revisions/result-revision'))return new Response(JSON.stringify({ref:exact,kind:'interview_record',title:'后来关联成果',body:'准确正文',objects:[],changes:[],base_standard_ref:null,basis:[]}));
  return new Response('{}',{status:404});
 });
 const createUrl=vi.fn().mockReturnValue('blob:test');Object.defineProperty(URL,'createObjectURL',{configurable:true,value:createUrl});Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:vi.fn()});const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));await settle();
  const open=[...host.querySelectorAll('summary')].find(b=>b.textContent==='查看正文')!;await act(async()=>open.dispatchEvent(new MouseEvent('click',{bubbles:true})));expect(host.textContent).toContain('准确正文');
  await act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent==='下载 Markdown')!.click());await settle();
  expect(fetcher.mock.calls.some(([u])=>String(u).endsWith('/results/cloud-result/revisions/result-revision/file'))).toBe(true);expect(click).toHaveBeenCalled();
 }finally{await act(async()=>root.unmount());fetcher.mockRestore();click.mockRestore();}
});
it('keeps five stages and the ordinary main-dialogue draft entry',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 const host=document.createElement('div');const root=createRoot(host);const onDraft=vi.fn();
 const reference={resultId:'result-a',turnId:'turn-a',conversationId:'chat-a',schemaId:'hr.candidate-interview-plan.v1',title:'DQE 候选人面试方案',contentSha256:'a'.repeat(64),createdAt:'2026-09-12T01:00:00Z',positionCandidateIds:['candidate-a']};
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);if(url.includes('/api/v1/hr/positions/'))return new Response(JSON.stringify({positionId:'position-a',items:[reference],nextOffset:null}));if(url.includes('/standards/current'))return new Response(JSON.stringify({code:'not_found'}),{status:404});if(url.includes('/results?'))return new Response(JSON.stringify({items:[],next_cursor:null}));return new Response('{}',{status:404})});
 const api={position:vi.fn().mockResolvedValue({positionId:'position-a',title:'DQE 工程师',locations:['深圳'],department:'研发',internalStatus:'active',sourceVersion:null})} as unknown as HrApi;
 const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 try{
  await act(async()=>root.render(<HrPositionWorkflow account={{csrf_token:'t',hard_stale_read_only:false} as Account} positionId="position-a" api={api} r12={r12} onDraft={onDraft}/>));
  expect(host.querySelectorAll('nav[aria-label="岗位全工作流"] button')).toHaveLength(5);
  expect(host.textContent).toContain('尚待确认');expect(host.textContent).not.toContain('已合并到主对话');
  await act(async()=>[...host.querySelectorAll('nav button')].find(b=>b.textContent?.includes('面试方案'))!.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  expect(host.textContent).toContain('面试方案与实际记录');expect(host.querySelector('a[href="/hr/conversations/chat-a"]')).toBeNull();
  await act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent?.includes('在主对话中推进'))!.click());
  expect(onDraft).toHaveBeenCalledWith(expect.objectContaining({text:expect.stringContaining('DQE 工程师')}),'position-a');
  expect(fetcher.mock.calls.every(([,init])=>!init?.method||init.method==='GET')).toBe(true);
 }finally{await act(async()=>root.unmount());fetcher.mockRestore();}
});
it('reports unreadable results instead of presenting empty work as confirmed absence',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);
 const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('{}',{status:503}));
 const api={position:vi.fn().mockResolvedValue({positionId:'p',title:'岗位',locations:[],internalStatus:'active'})} as unknown as HrApi;
 const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 try{await act(async()=>root.render(<HrPositionWorkflow account={{internal_user_id:'owner'} as Account} positionId="p" api={api} r12={r12} onDraft={()=>{}}/>));expect(host.textContent).toContain('已保存成果读取失败');expect(host.textContent).not.toContain('0 份已保存建议');expect(host.textContent).not.toContain('这个岗位尚无已保存成果');}
 finally{await act(async()=>root.unmount());fetcher.mockRestore();}
});

it('keeps a non-404 standard failure distinct from no confirmed standard',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);
 const api={position:vi.fn().mockResolvedValue(position)} as unknown as HrApi;const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([]),officialVersions:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);if(url.includes('/api/v1/hr/positions/'))return new Response(JSON.stringify({positionId:'position-a',items:[],nextOffset:null}));if(url.includes('/standards/current'))return new Response(JSON.stringify({code:'dependency_unavailable'}),{status:503});return new Response(JSON.stringify({items:[],next_cursor:null}))});
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));expect(host.textContent).toContain('当前标准读取失败');expect(host.textContent).not.toContain('尚待确认')}
 finally{await act(async()=>root.unmount());fetcher.mockRestore()}
});

it('fails closed when a cloud position read is forbidden',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);
 const api={position:vi.fn().mockResolvedValue(position)} as unknown as HrApi;const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>String(input).includes('/standards/current')?new Response(JSON.stringify({code:'forbidden'}),{status:403}):String(input).includes('/api/v1/hr/positions/')?new Response(JSON.stringify({positionId:'position-a',items:[],nextOffset:null})):new Response(JSON.stringify({items:[],next_cursor:null})));
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));expect(host.textContent).toContain('岗位暂时无法读取');expect(host.textContent).not.toContain('DQE 工程师')}
 finally{await act(async()=>root.unmount());fetcher.mockRestore()}
});

it('does not restore a late cloud response after the position changes',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);let releaseA!:(response:Response)=>void;const pendingA=new Promise<Response>(resolve=>{releaseA=resolve});
 const api={position:vi.fn(async(id:string)=>({...position,positionId:id,title:id==='position-a'?'岗位 A':'岗位 B'}))} as unknown as HrApi;const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);if(url.includes('/api/v1/hr/positions/')){const id=url.includes('position-b')?'position-b':'position-a';return new Response(JSON.stringify({positionId:id,items:[],nextOffset:null}))}if(url.includes('/position-a/standards/current'))return pendingA;if(url.includes('/position-b/standards/current'))return new Response(JSON.stringify({ref:{...exact,kind:'standard',id:'position-b'},position_id:'position-b',items:[{item_id:'b',text:'B 当前标准'}],selected_change_ids:['b'],confirmed_at:'2026-09-14T02:00:00Z'}));return new Response(JSON.stringify({items:[],next_cursor:null}))});
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-b" api={api} r12={r12} onDraft={()=>{}}/>));await settle();expect(host.textContent).toContain('岗位 B');releaseA(new Response(JSON.stringify({ref:{...exact,kind:'standard',id:'position-a'},position_id:'position-a',items:[{item_id:'a',text:'A 私有标准'}],selected_change_ids:['a'],confirmed_at:'2026-09-14T01:00:00Z'})));await settle();expect(host.textContent).not.toContain('岗位 A');expect(host.textContent).not.toContain('A 私有标准')}
 finally{await act(async()=>root.unmount());fetcher.mockRestore()}
});

it('does not create a download after the workflow unmounts',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);let release!:(response:Response)=>void;const pending=new Promise<Response>(resolve=>{release=resolve});
 const api={position:vi.fn().mockResolvedValue(position)} as unknown as HrApi;const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 const createUrl=vi.fn().mockReturnValue('blob:test');Object.defineProperty(URL,'createObjectURL',{configurable:true,value:createUrl});const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);if(url.includes('/api/v1/hr/positions/'))return new Response(JSON.stringify({positionId:'position-a',items:[],nextOffset:null}));if(url.includes('/standards/current'))return new Response(JSON.stringify({code:'not_found'}),{status:404});if(url.includes('/results?'))return new Response(JSON.stringify({items:[{ref:exact,title:'成果',description:''}],next_cursor:null}));if(url.endsWith('/file'))return pending;return new Response(JSON.stringify({ref:exact,kind:'sourcing',title:'成果',body:'正文',objects:[],changes:[],base_standard_ref:null,basis:[]}))});
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));await act(async()=>[...host.querySelectorAll('button')].find(button=>button.textContent==='下载 Markdown')!.click());await act(async()=>root.unmount());release(new Response('late'));await settle();expect(createUrl).not.toHaveBeenCalled();expect(click).not.toHaveBeenCalled()}
 finally{fetcher.mockRestore();click.mockRestore()}
});

it.each([401,403])('invalidates all pending reads when a later result body returns %s',async status=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);let release!:(response:Response)=>void;const pending=new Promise<Response>(resolve=>{release=resolve});const second={...exact,id:'second'};
 const api={position:vi.fn().mockResolvedValue(position)} as unknown as HrApi;const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([]),officialVersions:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);if(url.includes('/api/v1/hr/positions/'))return new Response(JSON.stringify({positionId:'position-a',items:[],nextOffset:null}));if(url.includes('/standards/current'))return new Response(JSON.stringify({ref:{...exact,kind:'standard'},position_id:'position-a',items:[{item_id:'private',text:'受保护标准'}],selected_change_ids:['private'],confirmed_at:'2026-09-14T02:00:00Z'}));if(url.includes('/results?'))return new Response(JSON.stringify({items:[{ref:exact,title:'先失败',description:''},{ref:second,title:'后鉴权',description:''}],next_cursor:null}));if(url.includes('/cloud-result/'))return new Response(JSON.stringify({code:'dependency_unavailable'}),{status:503});if(url.includes('/second/'))return pending;return new Response('{}',{status:404})});
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));await settle();await act(async()=>[...host.querySelectorAll<HTMLButtonElement>('nav button')].find(button=>button.textContent?.includes('JD / JR'))!.click());expect(host.textContent).toContain('受保护标准');release(new Response(JSON.stringify({code:'forbidden'}),{status}));await settle();expect(host.textContent).toContain('岗位暂时无法读取');expect(host.textContent).not.toContain('受保护标准')}
 finally{await act(async()=>root.unmount());fetcher.mockRestore()}
});

it.each([401,403])('does not deliver another pending download after a concurrent %s',async status=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);let fail!:(response:Response)=>void,late!:(response:Response)=>void;const first=new Promise<Response>(resolve=>{fail=resolve}),secondDownload=new Promise<Response>(resolve=>{late=resolve});const second={...exact,id:'second'};
 const api={position:vi.fn().mockResolvedValue(position)} as unknown as HrApi;const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;const createUrl=vi.fn();Object.defineProperty(URL,'createObjectURL',{configurable:true,value:createUrl});const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);if(url.includes('/api/v1/hr/positions/'))return new Response(JSON.stringify({positionId:'position-a',items:[],nextOffset:null}));if(url.includes('/standards/current'))return new Response(JSON.stringify({code:'not_found'}),{status:404});if(url.includes('/results?'))return new Response(JSON.stringify({items:[{ref:exact,title:'一',description:''},{ref:second,title:'二',description:''}],next_cursor:null}));if(url.endsWith('/file'))return url.includes('/second/')?secondDownload:first;const ref=url.includes('/second/')?second:exact;return new Response(JSON.stringify({ref,kind:'sourcing',title:ref.id,body:'正文',objects:[],changes:[],base_standard_ref:null,basis:[]}))});
 try{await act(async()=>root.render(<HrPositionWorkflow account={account} positionId="position-a" api={api} r12={r12} onDraft={()=>{}}/>));const buttons=[...host.querySelectorAll('button')].filter(button=>button.textContent==='下载 Markdown');await act(async()=>{buttons[0].click();buttons[1].click()});fail(new Response(JSON.stringify({code:'forbidden'}),{status}));await settle();late(new Response('late'));await settle();expect(host.textContent).toContain('岗位暂时无法读取');expect(createUrl).not.toHaveBeenCalled();expect(click).not.toHaveBeenCalled()}
 finally{await act(async()=>root.unmount());fetcher.mockRestore();click.mockRestore()}
});
