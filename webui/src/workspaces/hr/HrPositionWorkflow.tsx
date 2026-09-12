import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowUpRight, RefreshCw } from 'lucide-react';
import { platformPath, type Account } from '../../auth';
import type { HrApi } from '../../hrApi';
import type { HrR12Api } from '../../hrR12Api';
import type { HrContextVersion, HrPositionCandidate, HrPositionSection } from '../../hrR12Types';
import type { HrPositionDetail } from '../../hrTypes';
import type { HrComposerDraft } from '../../conversationTypes';
import { PlatformLink } from '../../components/PlatformLink';
import { HrOfficialPositionPanel } from './HrOfficialPositionPanel';
import { HrContextVersionView } from './HrPositionContextPanel';
import { HrCandidateWorkspace } from './HrCandidateWorkspace';
import { HrPositionResourcesPanel } from './HrPositionResourcesPanel';
import { HrSavedResultCard, type ResultRef } from './HrTurnResults';
import './hrPositionWorkflow.css';

type Stage='overview'|'requirements'|'sourcing'|'candidates'|'interviews'|'review'|'resources';
type PositionResult=ResultRef & {conversationId:string;createdAt:string;positionCandidateIds:string[]};
const stages: {id:Stage;title:string;description:string}[]=[
 {id:'requirements',title:'JD / JR',description:'官网原文 · 已确认标准'},
 {id:'sourcing',title:'人才搜寻',description:'人才画像 · 搜寻策略'},
 {id:'candidates',title:'候选人',description:'简历材料 · 人岗分析'},
 {id:'interviews',title:'面试',description:'面试方案 · 实际记录'},
 {id:'review',title:'招聘复盘',description:'证据分歧 · 标准调整'},
];
const kinds:Record<string,string>={'hr.analysis.v1':'岗位分析','hr.standard-proposal.v1':'已保存标准建议','hr.candidate-analysis.v1':'候选人分析','hr.candidate-analysis.v2':'候选人分析','hr.candidate-interview-plan.v1':'面试方案','hr.candidate-interview-record.v1':'面试记录'};
export function HrPositionWorkflow({account,positionId,section,api,r12,onDraft,draftError}:{account:Account;positionId:string;section?:HrPositionSection;draftError?:string;api:HrApi;r12:HrR12Api;onDraft:(value:HrComposerDraft,positionId:string|null)=>void}){
 const [stage,setStage]=useState<Stage>(section==='context'?'requirements':section==='candidates'?'candidates':section==='artifacts'?'resources':'overview');
 const [position,setPosition]=useState<HrPositionDetail|null>(null);
 const [results,setResults]=useState<PositionResult[]>([]);
 const [context,setContext]=useState<{current:HrContextVersion|null;history:HrContextVersion[]}|null>(null);
 const [candidates,setCandidates]=useState<HrPositionCandidate[]|null>(null);
 const [errors,setErrors]=useState<string[]>([]);const [failure,setFailure]=useState(false);const [loading,setLoading]=useState(true);const [revision,setRevision]=useState(0);
 useEffect(()=>{setStage(section==='context'?'requirements':section==='candidates'?'candidates':section==='artifacts'?'resources':'overview');},[section]);
 useEffect(()=>{
  const controller=new AbortController();setLoading(true);setFailure(false);setErrors([]);setPosition(null);setResults([]);setContext(null);setCandidates(null);
  const loadResults=async()=>{let offset:number|null=0;const items:PositionResult[]=[];const seen=new Set<number>();while(offset!==null){if(seen.has(offset))throw new Error('invalid pagination');seen.add(offset);
   const response:Response=await fetch(platformPath(`/api/v1/hr/positions/${encodeURIComponent(positionId)}/results?offset=${offset}&limit=50`),{credentials:'same-origin',signal:controller.signal});
   if(!response.ok)throw Object.assign(new Error('results unavailable'),{status:response.status});const page:{positionId:string;items:PositionResult[];nextOffset:number|null}=await response.json();
   if(page.positionId!==positionId||!Array.isArray(page.items)||!(page.nextOffset===null||Number.isSafeInteger(page.nextOffset)&&page.nextOffset>offset))throw new Error('result scope invalid');
   items.push(...page.items);offset=page.nextOffset;
  }return items;};
  void(async()=>{try{
   const detail=await api.position(positionId,controller.signal);
   const [saved,standard,people]=await Promise.allSettled([loadResults(),r12.context(positionId,controller.signal),r12.positionCandidates(positionId,controller.signal)]);
   if(controller.signal.aborted)return;
   if([saved,standard,people].some(value=>value.status==='rejected'&&[401,403].includes(value.reason?.status)))throw new Error('access denied');
   setPosition(detail);setResults(saved.status==='fulfilled'?saved.value:[]);setContext(standard.status==='fulfilled'?standard.value:null);setCandidates(people.status==='fulfilled'?people.value:null);
   setErrors([saved.status==='rejected'?'保存成果读取失败':null,standard.status==='rejected'?'岗位标准读取失败':null,people.status==='rejected'?'候选人读取失败':null].filter((value):value is string=>Boolean(value)));
  }catch{if(!controller.signal.aborted)setFailure(true);}finally{if(!controller.signal.aborted)setLoading(false);}})();
  return()=>controller.abort();
 },[api,r12,positionId,revision]);
 const readOnly=account.hard_stale_read_only||position?.internalStatus!=='active';
 const choose=(value:Stage)=>{setStage(value);};
 const draft=(text:string)=>onDraft({id:crypto.randomUUID(),text},positionId);
 const standardResults=results.filter(r=>r.schemaId==='hr.standard-proposal.v1');
 const interviewResults=results.filter(r=>r.schemaId.includes('interview'));
 const analysis=results.filter(r=>r.schemaId==='hr.analysis.v1');
 const candidateResults=results.filter(r=>r.schemaId.startsWith('hr.candidate-analysis.'));
 function saved(items:PositionResult[],empty:string){return <div className="hr-pw-saved">{errors.includes('保存成果读取失败')?<p role="alert">保存成果读取失败，请使用上方刷新重试。</p>:!items.length?<p className="hr-pw-empty">{empty}</p>:items.map(result=><article key={result.resultId}><div className="hr-pw-result-meta"><span>{kinds[result.schemaId]??'已保存成果'} · {new Date(result.createdAt).toLocaleDateString('zh-CN')}</span><PlatformLink href={`/hr/conversations/${encodeURIComponent(result.conversationId)}`}>来源对话 <ArrowUpRight size={13}/></PlatformLink></div><HrSavedResultCard reference={result} readOnly={readOnly} onDraft={onDraft}/></article>)}</div>;}
 return <main className="hr-position-workflow"><div className="hr-position-page-inner"><PlatformLink className="hr-pw-back" href="/hr/positions"><ArrowLeft size={16}/>全部岗位</PlatformLink>
 {loading?<p className="hr-pw-empty" role="status">正在汇集岗位资料与保存成果…</p>:failure||!position?<div className="hr-pw-empty" role="alert"><h2>岗位暂时无法读取</h2><p>请确认当前账号有访问权限，或重新读取。</p><button onClick={()=>setRevision(v=>v+1)}>重新读取</button></div>:<>
 <header className="hr-pw-heading"><div><span>{position.officialJobId??'自建岗位'} · {position.internalStatus==='archived'?'已归档':'岗位工作流'}</span><h1>{position.title}</h1><p>{[position.department,...position.locations].filter(Boolean).join(' · ')}</p></div><div className="hr-pw-heading-actions"><button title="刷新岗位资料" aria-label="刷新岗位资料" onClick={()=>setRevision(v=>v+1)}><RefreshCw size={17}/></button><button className="hr-pw-primary" disabled={readOnly} onClick={()=>draft(`请结合《${position.title}》已有的岗位要求与保存成果，和我一起推进这个岗位。`)}>在主对话中推进 <ArrowUpRight size={16}/></button></div></header>
 {errors.length>0&&<p role="alert" className="hr-pw-notice">{errors.join('；')}。其他已读取资料仍可查看，请使用上方刷新重试。</p>}
 {draftError&&<p role="alert" className="hr-pw-notice">{draftError}</p>}
 <nav className="hr-pw-flow" aria-label="岗位全工作流">{stages.map((item,index)=><button aria-current={stage===item.id?'step':undefined} key={item.id} onClick={()=>choose(item.id)}><span>{String(index+1).padStart(2,'0')}</span><strong>{item.title}</strong><small>{item.description}</small></button>)}</nav>
 <div className="hr-pw-subnav"><button aria-pressed={stage==='overview'} onClick={()=>choose('overview')}>岗位总览</button><button aria-pressed={stage==='resources'} onClick={()=>choose('resources')}>材料与文件</button><span>成果与真实记录汇集于此，讨论继续在主对话。</span></div>
 <div className="hr-pw-content">
 {stage==='overview'&&<><div className="hr-pw-summary"><button onClick={()=>choose('requirements')}><span>岗位标准</span><strong>{context?(context.current?'已有确认标准':'尚待确认'):'读取失败'}</strong><small>{errors.includes('保存成果读取失败')?'保存建议读取失败':`${standardResults.length} 份已保存建议`}</small></button><button onClick={()=>choose('candidates')}><span>候选人档案</span><strong>{candidates?`${candidates.length} 位`:'读取失败'}</strong><small>简历与人岗分析</small></button><button onClick={()=>choose('interviews')}><span>面试成果</span><strong>{errors.includes('保存成果读取失败')?'读取失败':`${interviewResults.length} 份`}</strong><small>方案与实际记录分别保存</small></button></div><section className="hr-pw-section"><h2>这份岗位的工作记录</h2><p>官网要求、确认标准和对话成果分别保存。点开上方阶段查看具体内容，已有成果也可以直接展开并继续引用。</p>{saved(results,'这个岗位尚无已保存成果。可以先查看 JD / JR，或回主对话讨论；已结束但未保存成果的回答不会列在这里。')}</section></>}
 {stage==='requirements'&&<><section className="hr-pw-section"><h2>岗位要求与确认标准</h2><p>官网原文是来源事实，确认标准是团队已经认可的要求，建议在确认前单独保留。</p></section><HrOfficialPositionPanel api={r12} positionId={positionId} currentSourceVersion={position.sourceVersion} fallback={position}/><section className="hr-pw-section"><h2>已确认岗位标准</h2>{context?.current?<HrContextVersionView version={context.current}/>:<p>{context?'尚无已确认标准。可以基于官网 JD / JR 继续讨论。':'岗位标准暂时无法读取。'}</p>}{Boolean(context?.history.length)&&<details><summary>历史确认记录</summary>{context?.history.filter(v=>v.status==='superseded').map(v=><HrContextVersionView key={v.contextVersionId} version={v}/>)}</details>}</section><section className="hr-pw-section"><h2>已保存的标准建议</h2>{saved(standardResults,'尚无已保存的岗位标准建议。')}<button disabled={readOnly} onClick={()=>draft(`请梳理《${position.title}》的 JD / JR，读取官网原文及已确认标准，列出需要澄清和调整的要求，供我逐项确认。`)}>在对话中梳理要求</button></section></>}
 {(stage==='sourcing'||stage==='review')&&<section className="hr-pw-section"><h2>{stage==='sourcing'?'人才搜寻与吸引':'招聘复盘与标准调整'}</h2><p>{stage==='sourcing'?'查看人才画像、搜寻方向、来源与沟通建议。':'结合候选人和面试的真实证据，整理共性问题与后续调整。'}</p><button disabled={readOnly} onClick={()=>draft(stage==='sourcing'?`请为《${position.title}》制定人才搜寻策略，先读取当前要求与已有成果，明确目标背景、可迁移能力、渠道和需要补充的信息。`:`请结合《${position.title}》已有的岗位要求、候选人分析和面试记录做招聘复盘，区分实际证据、共性问题与待确认的标准调整；没有记录的部分明确列出。`)}>带着这个岗位继续讨论</button><h3>岗位分析成果</h3><p className="hr-pw-notice">已有岗位分析尚未单独标注搜寻或复盘类型，两处共用这些成果；请按正文查看其实际内容。</p>{saved(analysis,'尚无已保存的岗位分析成果。')}</section>}
 {stage==='candidates'&&<><section className="hr-pw-section"><h2>候选人与简历</h2><p>每位候选人的材料、分析与面试依据都在当前岗位范围内组织。</p></section><HrCandidateWorkspace api={r12} positionId={positionId} csrfToken={account.csrf_token} currentContextVersionId={context?.current?.contextVersionId??null} onCandidatesChange={setCandidates} readOnly={readOnly} onDraft={(text,ids,attachments)=>onDraft({id:crypto.randomUUID(),text,positionCandidateIds:ids,attachmentIds:attachments},positionId)}/><section className="hr-pw-section"><h2>主对话保存的候选人分析</h2>{saved(candidateResults,'尚无主对话保存的候选人分析。')}</section></>}
 {stage==='interviews'&&<section className="hr-pw-section"><h2>面试方案与实际记录</h2><p>方案说明要验证什么，记录保留实际回答和待核验事项。展开具体成果，可携带它继续准备面试或整理记录。</p><button onClick={()=>choose('candidates')}>选择候选人与简历</button>{saved(interviewResults,'尚无已保存的面试方案或实际记录。先从候选人与简历中选择具体对象，再准备面试。')}</section>}
 {stage==='resources'&&<HrPositionResourcesPanel api={r12} positionId={positionId} readOnly={account.hard_stale_read_only}/>}
 </div></>}
 </div></main>;
}
