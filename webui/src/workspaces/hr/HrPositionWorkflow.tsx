import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowLeft,ArrowUpRight,Download,RefreshCw} from 'lucide-react';
import type {Account} from '../../auth';
import type {HrApi} from '../../hrApi';
import {createHrLoopApi,HrLoopError,readPages,type ExactRef,type SavedResult,type StandardView} from '../../hrLoopApi';
import type {HrR12Api} from '../../hrR12Api';
import type {HrPositionSection} from '../../hrR12Types';
import type {HrPositionDetail} from '../../hrTypes';
import type {HrComposerDraft} from '../../conversationTypes';
import {MessageMarkdown} from '../../components/MessageMarkdown';
import {PlatformLink} from '../../components/PlatformLink';
import {HrOfficialPositionPanel} from './HrOfficialPositionPanel';
import {HrPositionResourcesPanel} from './HrPositionResourcesPanel';
import './hrPositionWorkflow.css';

type Stage='overview'|'requirements'|'sourcing'|'candidates'|'interviews'|'review'|'resources';
type ReadState<T>={value:T;error:boolean};
const stages:{id:Stage;title:string;description:string}[]=[{id:'requirements',title:'JD / JR',description:'官网原文 · 已确认标准'},{id:'sourcing',title:'人才搜寻',description:'人才画像 · 搜寻策略'},{id:'candidates',title:'候选人',description:'简历材料 · 人岗分析'},{id:'interviews',title:'面试',description:'面试方案 · 实际记录'},{id:'review',title:'招聘复盘',description:'证据分歧 · 标准调整'}];
const groups={requirements:new Set(['role_calibration','jd','requirements','standard_proposal']),sourcing:new Set(['sourcing']),candidates:new Set(['candidate_assessment']),interviews:new Set(['interview_plan','interview_record']),review:new Set(['retrospective'])};
export function positionResultStage(kind:string):keyof typeof groups|null {
 for(const [stage,kinds] of Object.entries(groups))if(kinds.has(kind))return stage as keyof typeof groups;
 return null;
}
const cloudLabels:Record<string,string>={role_calibration:'岗位校准',jd:'JD / JR',requirements:'岗位要求',standard_proposal:'标准建议',sourcing:'搜寻策略',candidate_assessment:'候选人评估',interview_plan:'面试方案',interview_record:'面试记录',retrospective:'招聘复盘'};
const refKey=(ref:ExactRef)=>`${ref.kind}:${ref.id}:${ref.revision}:${ref.sha256}`;

function CloudCard({result,onDownload}:{result:SavedResult;onDownload:(result:SavedResult)=>void}){return <article className="hr-pw-cloud-result"><div className="hr-pw-result-meta"><span>{cloudLabels[result.kind]??'已保存成果'}</span><span>已保存成果</span></div><h3>{result.title}</h3><details><summary>查看正文</summary><MessageMarkdown content={result.body}/></details><button type="button" onClick={()=>onDownload(result)}><Download size={15}/>下载 Markdown</button></article>}

export function HrPositionWorkflow({account,positionId,section,api,r12,onDraft,draftError}:{account:Account;positionId:string;section?:HrPositionSection;draftError?:string;api:HrApi;r12:HrR12Api;onDraft:(value:HrComposerDraft,positionId:string|null)=>void}){
 const initial=():Stage=>section==='context'?'requirements':section==='candidates'?'candidates':section==='artifacts'?'resources':'overview';
 const [stage,setStage]=useState<Stage>(initial),[position,setPosition]=useState<HrPositionDetail|null>(null);
 const [standard,setStandard]=useState<ReadState<StandardView|null>>({value:null,error:false}),[cloudResults,setCloudResults]=useState<ReadState<SavedResult[]>>({value:[],error:false}),[failure,setFailure]=useState(false),[loading,setLoading]=useState(true),[revision,setRevision]=useState(0),[downloadError,setDownloadError]=useState(false);
 const epoch=useRef(0),cloudApi=useMemo(()=>createHrLoopApi(account.csrf_token),[account.csrf_token]);
 useEffect(()=>setStage(initial()),[section]);
 useEffect(()=>{
  const token=++epoch.current;
  const controller=new AbortController();
  const current=()=>epoch.current===token&&!controller.signal.aborted;
  const deny=()=>{if(!current())return;epoch.current++;setFailure(true);setPosition(null);setLoading(false)};
  const observeAuth=<T,>(promise:Promise<T>)=>promise.catch(error=>{if(error instanceof HrLoopError&&[401,403].includes(error.status))deny();throw error});
  setLoading(true);setFailure(false);setPosition(null);setStandard({value:null,error:false});setCloudResults({value:[],error:false});setDownloadError(false);
  async function readCloudResults(){
   const items=await observeAuth(readPages(cursor=>observeAuth(cloudApi.results({position:positionId,cursor})),current));
   return Promise.all(items.map(async item=>{
    const result=await observeAuth(cloudApi.result(item.ref));
    if(refKey(result.ref)!==refKey(item.ref))throw new Error('result reference mismatch');
    return result;
   }));
  }
  void(async()=>{try{
   const detail=await api.position(positionId,controller.signal);
   if(!current())return;
   setPosition(detail);
   const [newStandard,newResults]=await Promise.allSettled([
    observeAuth(cloudApi.standard(positionId)).catch(error=>{if(error instanceof HrLoopError&&error.status===404&&error.code==='not_found')return null;throw error}),
    readCloudResults(),
   ]);
   if(!current())return;
   setStandard(newStandard.status==='fulfilled'?{value:newStandard.value,error:false}:{value:null,error:true});
   setCloudResults(newResults.status==='fulfilled'?{value:newResults.value,error:false}:{value:[],error:true});
  }catch{if(current())setFailure(true)}finally{if(current())setLoading(false)}})();
  return()=>{controller.abort();if(epoch.current===token)epoch.current++}
 },[account.internal_user_id,account.csrf_token,api,r12,positionId,revision,cloudApi]);
 const readOnly=account.hard_stale_read_only||position?.internalStatus!=='active',draft=(text:string)=>onDraft({id:crypto.randomUUID(),text},positionId),grouped=(name:keyof typeof groups)=>cloudResults.value.filter(result=>positionResultStage(result.kind)===name);
 async function download(result:SavedResult){
  const token=epoch.current;
  setDownloadError(false);
  try{
   const blob=await cloudApi.download(result.ref);
   if(epoch.current!==token)return;
   const url=URL.createObjectURL(blob),anchor=document.createElement('a');
   anchor.href=url;anchor.download=`${result.title.replace(/[\\/:*?"<>|]/g,'_')}.md`;anchor.click();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){
   if(epoch.current!==token)return;
   if(error instanceof HrLoopError&&[401,403].includes(error.status)){
    epoch.current++;setFailure(true);setPosition(null);return;
   }
   setDownloadError(true);
  }
 }
 function cloud(items:SavedResult[],empty:string){return <div className="hr-pw-saved">{cloudResults.error?<p role="alert">已保存成果读取失败，请使用上方刷新重试。</p>:items.length===0?<p className="hr-pw-empty">{empty}</p>:items.map(result=><CloudCard key={refKey(result.ref)} result={result} onDownload={download}/>)}</div>}
 if(loading)return <main className="hr-position-workflow"><div className="hr-position-page-inner"><p className="hr-pw-empty" role="status">正在汇集岗位资料与保存成果…</p></div></main>;
 if(failure||!position)return <main className="hr-position-workflow"><div className="hr-position-page-inner"><div className="hr-pw-empty" role="alert"><h2>岗位暂时无法读取</h2><p>请确认当前账号有访问权限，或重新读取。</p><button onClick={()=>setRevision(v=>v+1)}>重新读取</button></div></div></main>;
 const notices=[standard.error?'当前标准读取失败':null,cloudResults.error?'已保存成果读取失败':null,downloadError?'成果下载失败':null].filter(Boolean).join('；');
 return <main className="hr-position-workflow"><div className="hr-position-page-inner"><PlatformLink className="hr-pw-back" href="/hr/positions"><ArrowLeft size={16}/>全部岗位</PlatformLink><header className="hr-pw-heading"><div><span>{position.officialJobId??'自建岗位'} · {position.internalStatus==='archived'?'已归档':'岗位工作流'}</span><h1>{position.title}</h1><p>{[position.department,...position.locations].filter(Boolean).join(' · ')}</p></div><div className="hr-pw-heading-actions"><button title="刷新岗位资料" aria-label="刷新岗位资料" onClick={()=>setRevision(v=>v+1)}><RefreshCw size={17}/></button><button className="hr-pw-primary" disabled={readOnly} onClick={()=>draft(`请结合《${position.title}》已有的岗位要求与保存成果，和我一起推进这个岗位。`)}>在主对话中推进 <ArrowUpRight size={16}/></button></div></header>{notices&&<p role="alert" className="hr-pw-notice">{notices}。其他已读取资料仍可查看，请使用上方刷新重试。</p>}{draftError&&<p role="alert" className="hr-pw-notice">{draftError}</p>}<nav className="hr-pw-flow" aria-label="岗位全工作流">{stages.map((item,index)=><button aria-current={stage===item.id?'step':undefined} key={item.id} onClick={()=>setStage(item.id)}><span>{String(index+1).padStart(2,'0')}</span><strong>{item.title}</strong><small>{item.description}</small></button>)}</nav><div className="hr-pw-subnav"><button aria-pressed={stage==='overview'} onClick={()=>setStage('overview')}>岗位总览</button><button aria-pressed={stage==='resources'} onClick={()=>setStage('resources')}>材料与文件</button><span>成果与真实记录汇集于此，讨论继续在主对话。</span></div><div className="hr-pw-content">
 {stage==='overview'&&<><div className="hr-pw-summary"><button onClick={()=>setStage('requirements')}><span>岗位标准</span><strong>{standard.error?'读取失败':standard.value?'已有确认标准':'尚待确认'}</strong><small>{cloudResults.error?'标准建议读取失败':`${grouped('requirements').filter(result=>result.kind==='standard_proposal').length} 份已保存建议`}</small></button><button onClick={()=>setStage('candidates')}><span>候选人评估</span><strong>{cloudResults.error?'读取失败':`${grouped('candidates').length} 份`}</strong><small>当前岗位已保存成果</small></button><button onClick={()=>setStage('interviews')}><span>面试成果</span><strong>{cloudResults.error?'读取失败':`${grouped('interviews').length} 份`}</strong><small>方案与实际记录分别保存</small></button></div><section className="hr-pw-section"><h2>这份岗位的当前成果</h2><p>官网要求、已确认标准和已保存成果分别保存。讨论继续在主对话。</p>{cloud(cloudResults.value.filter(result=>result.kind!=='research'),'这个岗位尚无已保存成果。')}</section></>}
 {stage==='requirements'&&<><section className="hr-pw-section"><h2>岗位要求与确认标准</h2><p>官网原文是来源事实，确认标准是团队已经认可的要求，建议在确认前单独保留。</p></section><HrOfficialPositionPanel api={r12} positionId={positionId} currentSourceVersion={position.sourceVersion} fallback={position}/><section className="hr-pw-section"><h2>当前已确认岗位标准</h2>{standard.error?<p role="alert">当前标准暂时无法读取。</p>:standard.value?<><time dateTime={standard.value.confirmed_at}>确认于 {new Date(standard.value.confirmed_at).toLocaleString('zh-CN')}</time><ul>{standard.value.items.map(item=><li key={item.item_id}>{item.text}</li>)}</ul></>:<p>尚无已确认标准。可以基于官网 JD / JR 继续讨论。</p>}</section><section className="hr-pw-section"><h2>已保存的要求与建议</h2>{cloud(grouped('requirements'),'尚无已保存的岗位要求或标准建议。')}<button disabled={readOnly} onClick={()=>draft(`请梳理《${position.title}》的 JD / JR，读取官网原文及已确认标准，列出需要澄清和调整的要求，供我逐项确认。`)}>在对话中梳理要求</button></section></>}
 {stage==='sourcing'&&<section className="hr-pw-section"><h2>人才搜寻与吸引</h2><p>查看人才画像、搜寻方向、来源与沟通建议。</p><button disabled={readOnly} onClick={()=>draft(`请为《${position.title}》制定人才搜寻策略，先读取当前要求与已有成果，明确目标背景、可迁移能力、渠道和需要补充的信息。`)}>带着这个岗位继续讨论</button>{cloud(grouped('sourcing'),'尚无已保存的搜寻成果。')}</section>}
 {stage==='review'&&<section className="hr-pw-section"><h2>招聘复盘与标准调整</h2><p>结合候选人和面试的真实证据，整理共性问题与后续调整。</p><button disabled={readOnly} onClick={()=>draft(`请结合《${position.title}》已有的岗位要求、候选人分析和面试记录做招聘复盘，区分实际证据、共性问题与待确认的标准调整；没有记录的部分明确列出。`)}>带着这个岗位继续讨论</button>{cloud(grouped('review'),'尚无已保存的招聘复盘。')}</section>}
 {stage==='candidates'&&<section className="hr-pw-section"><h2>候选人评估</h2><p>查看当前岗位已保存的候选人评估；新增或继续评估请回到主对话。</p><button disabled={readOnly} onClick={()=>draft(`请结合《${position.title}》的当前标准和已有成果，继续候选人评估。`)}>在主对话中评估候选人</button>{cloud(grouped('candidates'),'尚无已保存的候选人评估。')}</section>}
 {stage==='interviews'&&<section className="hr-pw-section"><h2>面试方案与实际记录</h2><p>方案说明要验证什么，记录保留实际回答和待核验事项。</p><button onClick={()=>setStage('candidates')}>选择候选人与简历</button>{cloud(grouped('interviews'),'尚无已保存的面试方案或实际记录。')}</section>}{stage==='resources'&&<HrPositionResourcesPanel api={r12} positionId={positionId} readOnly={account.hard_stale_read_only}/>}</div></div></main>
}
