/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { PanoramaWorkArea } from './PanoramaWorkArea';
import { BrainWorkspacePage } from './pages/BrainWorkspacePage';
import type { Route } from './router';

it('uses the real brain composer, preserves an in-flight request on collapse, and reopens its result without resubmission',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 const box=document.createElement('div');document.body.append(box);const root=createRoot(box);
 const account:any={internal_user_id:'owner',role:'platform_owner',display_name:'Owner',csrf_token:'csrf',workspace_scopes:[],hard_stale_read_only:false};
 const conversation:any={conversation_id:'c1',mode:'brain',direct_agent_id:null,title:'全景工作',status:'active',summary_through_seq:0,created_at:'2026-09-20T00:00:00Z',updated_at:'2026-09-20T00:00:00Z',archived_at:null};
 let finish!:(value:any)=>void;
 const send=vi.fn().mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 const brainClient={createSubmission:vi.fn().mockReturnValue({send,idempotencyKey:'one-request'})};
 const onNavigate=vi.fn();
 const list=vi.fn().mockResolvedValue({items:[],next_cursor:null});
 const conversationClient:any={fetchConversation:vi.fn().mockResolvedValue({conversation,current_turn:null}),fetchMessages:vi.fn().mockResolvedValue([{message_id:'m1',conversation_id:'c1',seq:1,role:'assistant',content:'这是接口返回的结果',turn_id:'t1',delivery_status:'completed',created_at:'2026-09-20T00:00:00Z',completed_at:'2026-09-20T00:00:00Z',input_attachments:[],output_attachments:[],active_attachment_ids:[]}]),streamEvents:vi.fn().mockResolvedValue(undefined),fetchTaskDetail:vi.fn(),createMessageSubmission:vi.fn(),cancelCurrentTurn:vi.fn(),confirmAction:vi.fn(),rejectAction:vi.fn(),submitFeedback:vi.fn(),reconnectDelay:vi.fn(),retryTurn:vi.fn()};
 const render=(route:Route)=><BrainWorkspacePage account={account} conversationId={route.name==='conversation'?route.conversationId:undefined} client={{list}} brainClient={brainClient} conversationClient={conversationClient} onNavigate={onNavigate}/>;
 const props={renderWorkspace:render,onClose:vi.fn(),onDirty:vi.fn()};
 try {
  await act(async()=>root.render(<PanoramaWorkArea {...props} route={{name:'brain'}}/>));
  const textarea=box.querySelector('textarea')!;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(textarea,'请整理本次工作');textarea.dispatchEvent(new Event('input',{bubbles:true}));});
  await act(async()=>box.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(send).toHaveBeenCalledOnce();const signal=send.mock.calls[0][0] as AbortSignal;
  await act(async()=>root.render(<PanoramaWorkArea {...props}/>));expect(signal.aborted).toBe(false);
  await act(async()=>finish({conversation}));expect(onNavigate).toHaveBeenCalledWith('/conversations/c1');
  await act(async()=>root.render(<PanoramaWorkArea {...props} route={{name:'conversation',conversationId:'c1'}}/>));
  expect(box.textContent).toContain('这是接口返回的结果');
  await act(async()=>root.render(<PanoramaWorkArea {...props}/>));
  await act(async()=>root.render(<PanoramaWorkArea {...props} route={{name:'conversation',conversationId:'c1'}}/>));
  expect(box.textContent).toContain('这是接口返回的结果');expect(send).toHaveBeenCalledOnce();
 } finally {await act(async()=>root.unmount());box.remove();}
});
