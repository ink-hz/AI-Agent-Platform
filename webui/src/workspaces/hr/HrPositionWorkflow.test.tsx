/** @vitest-environment jsdom */
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {it,expect,vi} from 'vitest';
import {HrPositionWorkflow} from './HrPositionWorkflow';
import type {Account} from '../../auth';
import type {HrApi} from '../../hrApi';
import type {HrR12Api} from '../../hrR12Api';
it('shows all stages and continues from a saved interview with the same candidate and result',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 const host=document.createElement('div');const root=createRoot(host);const onDraft=vi.fn();
 const reference={resultId:'result-a',turnId:'turn-a',conversationId:'chat-a',schemaId:'hr.candidate-interview-plan.v1',title:'DQE 候选人面试方案',contentSha256:'a'.repeat(64),createdAt:'2026-09-12T01:00:00Z',positionCandidateIds:['candidate-a']};
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>new Response(JSON.stringify(String(input).includes('/positions/')?{positionId:'position-a',items:[reference],nextOffset:null}:{positionId:'position-a',positionCandidateIds:['candidate-a'],candidateNames:['测试候选人'],attachmentIds:['resume-a'],result:{schemaId:reference.schemaId,title:reference.title,markdown:'核验质量问题定位证据'}})));
 const api={position:vi.fn().mockResolvedValue({positionId:'position-a',title:'DQE 工程师',locations:['深圳'],department:'研发',internalStatus:'active',sourceVersion:null})} as unknown as HrApi;
 const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 try{
  await act(async()=>root.render(<HrPositionWorkflow account={{csrf_token:'t',hard_stale_read_only:false} as Account} positionId="position-a" api={api} r12={r12} onDraft={onDraft}/>));
  expect(host.querySelectorAll('nav[aria-label="岗位全工作流"] button')).toHaveLength(5);
  expect(host.textContent).toContain('尚待确认');expect(host.textContent).not.toContain('已合并到主对话');
  await act(async()=>[...host.querySelectorAll('nav button')].find(b=>b.textContent?.includes('面试方案'))!.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  expect(host.textContent).toContain('面试方案与实际记录');expect(host.querySelector('a[href="/hr/conversations/chat-a"]')).not.toBeNull();
  await act(async()=>{const d=host.querySelector<HTMLDetailsElement>('.hr-turn-result')!;d.open=true;d.dispatchEvent(new Event('toggle'));});
  await act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent==='整理面试记录')!.click());
  expect(onDraft).toHaveBeenCalledWith(expect.objectContaining({positionCandidateIds:['candidate-a'],attachmentIds:['resume-a'],inputResults:[expect.objectContaining({resultId:'result-a',contentSha256:'a'.repeat(64)})]}),'position-a');
  expect(fetcher.mock.calls.every(([,init])=>!init?.method||init.method==='GET')).toBe(true);
 }finally{await act(async()=>root.unmount());fetcher.mockRestore();}
});
it('reports unreadable results instead of presenting empty work as confirmed absence',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement('div');const root=createRoot(host);
 const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('{}',{status:503}));
 const api={position:vi.fn().mockResolvedValue({positionId:'p',title:'岗位',locations:[],internalStatus:'active'})} as unknown as HrApi;
 const r12={context:vi.fn().mockResolvedValue({current:null,history:[]}),positionCandidates:vi.fn().mockResolvedValue([])} as unknown as HrR12Api;
 try{await act(async()=>root.render(<HrPositionWorkflow account={{} as Account} positionId="p" api={api} r12={r12} onDraft={()=>{}}/>));expect(host.textContent).toContain('保存成果读取失败');expect(host.textContent).not.toContain('0 份已保存建议');expect(host.textContent).not.toContain('这个岗位尚无已保存成果');}
 finally{await act(async()=>root.unmount());fetcher.mockRestore();}
});
