import { useEffect, useRef, useState } from "react";
import { platformPath } from "../../auth";
import type { HrComposerDraft, HrTurnScope } from "../../conversationTypes";
import { MessageMarkdown } from "../../components/MessageMarkdown";

type ResultRef={turnId:string;resultId:string;schemaId:string;contentSha256:string};
export interface HrConversationResults {error?:string;knowledgeReads?:{turnId:string;methodId:string;revision:string;teamCommit:string;sha256:string;toolUseId:string}[];turns:{turnId:string;scope:HrTurnScope;positionTitle:string|null}[];results:ResultRef[]}
export function useHrConversationResults(conversationId:string|undefined,revision:number){
  const [data,setData]=useState<HrConversationResults>({turns:[],results:[]});
  const previous=useRef<string|undefined>(undefined);
  useEffect(()=>{
    if(previous.current!==conversationId){setData({turns:[],results:[]});previous.current=conversationId;}
    if(!conversationId)return;
    const controller=new AbortController();
    fetch(platformPath(`/api/v1/hr/conversations/${encodeURIComponent(conversationId)}/results`),{credentials:'include',signal:controller.signal})
      .then(async response=>{if(!response.ok)throw new Error();return response.json();})
      .then(value=>{if(!controller.signal.aborted)setData(value);}).catch(()=>{if(!controller.signal.aborted)setData(value=>({...value,error:'岗位成果暂时无法读取，请稍后刷新对话。'}));});
    return ()=>controller.abort();
  },[conversationId,revision]);
  return data;
}
interface SavedResult {positionId:string|null;result:{schemaId:string;title:string;markdown?:string;baseContextVersionId?:string|null;changes?:{changeId:string;module:string;markdown:string}[];methodSteps?:{methodId:string;stepId:string;status:string;evidence:string}[]}}
export function HrTurnResults({turnId,data,readOnly,onDraft}:{turnId:string;data:HrConversationResults;readOnly:boolean;onDraft:(draft:HrComposerDraft,positionId:string|null)=>void}){
  const turn=data.turns.find(item=>item.turnId===turnId);
  const results=data.results.filter(item=>item.turnId===turnId);
  return <div className="hr-turn-context">{turn&&<small>本轮 · {turn.positionTitle??'通用对话'}{turn.scope.positionCandidateIds.length?` · ${turn.scope.positionCandidateIds.length} 位候选人`:''}</small>}
    {data.knowledgeReads?.filter(read=>read.turnId===turnId).map(read=><small key={read.toolUseId} title={`版本 ${read.teamCommit} · SHA-256 ${read.sha256}`}>已读取：{read.methodId} · r{read.revision}</small>)}
    {results.map(ref=><Result key={ref.resultId} reference={ref} readOnly={readOnly} onDraft={onDraft}/>)}</div>;
}
function Result({reference,readOnly,onDraft}:{reference:ResultRef;readOnly:boolean;onDraft:(draft:HrComposerDraft,positionId:string|null)=>void}){
  const [open,setOpen]=useState(false);const [saved,setSaved]=useState<SavedResult|null>(null);const [error,setError]=useState(false);const [selected,setSelected]=useState<string[]>([]);
  useEffect(()=>{if(!open||saved)return;const controller=new AbortController();setError(false);
    fetch(platformPath(`/api/v1/hr/results/${reference.resultId}`),{credentials:'include',signal:controller.signal}).then(async response=>{if(!response.ok)throw new Error();return response.json();})
      .then(value=>{if(!controller.signal.aborted)setSaved(value);}).catch(()=>{if(!controller.signal.aborted)setError(true);});return()=>controller.abort();
  },[open,saved,reference.resultId]);
  const result=saved?.result;
  return <details className="hr-turn-result" onToggle={event=>setOpen(event.currentTarget.open)}><summary>{result?.title??(reference.schemaId==='hr.standard-proposal.v1'?'查看岗位标准建议':'查看本轮成果')}</summary>
    {error?<p role="alert">成果暂时无法读取，请关闭后重新打开。</p>:!result?<p>正在读取…</p>:<>
      {result.markdown&&<MessageMarkdown content={result.markdown}/>}
      {result.changes?.map(change=><label className="hr-standard-change" key={change.changeId}><input type="checkbox" disabled={readOnly} checked={selected.includes(change.changeId)} onChange={event=>setSelected(ids=>event.target.checked?[...ids,change.changeId]:ids.filter(id=>id!==change.changeId))}/><span><MessageMarkdown content={change.markdown}/></span></label>)}
      {result.changes&&<><p>勾选认可的条目，再发送确认消息，才会保存为后续可复用的岗位标准。</p><button type="button" disabled={readOnly||selected.length===0} onClick={()=>onDraft({id:crypto.randomUUID(),text:`确认所选的 ${selected.length} 项岗位标准。`,standardConsent:{proposalResultId:reference.resultId,proposalContentSha256:reference.contentSha256,expectedContextVersionId:result.baseContextVersionId??null,selectedChangeIds:selected}},saved!.positionId)}>填入确认消息</button></>}
      {result.methodSteps&&result.methodSteps.length>0&&<details><summary>回答中说明的方法</summary><p>以下为模型对方法运用的说明；文件读取证据在本轮标记中单独展示。</p>{result.methodSteps.map((step,index)=><p key={index}>{step.methodId} · {step.stepId} · {step.status}：{step.evidence}</p>)}</details>}
    </>}
  </details>;
}
