import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, BriefcaseBusiness, Search } from 'lucide-react';
import type { Account } from "../../auth";
import { PlatformLink } from '../../components/PlatformLink';
import { createHrApi, type HrApi } from "../../hrApi";
import type { HrPosition } from "../../hrTypes";
import './hrPositionWorkflow.css';
export function HrPositionIndex({account,api:injectedApi}:{account:Account;api?:HrApi;onSelect?:(position:HrPosition)=>void}){
  const api=useMemo(()=>injectedApi??createHrApi(account.csrf_token),[injectedApi,account.csrf_token]);
  const [items,setItems]=useState<HrPosition[]>([]);const [query,setQuery]=useState('');const [error,setError]=useState(false);const [loading,setLoading]=useState(true);const [attempt,setAttempt]=useState(0);const [status,setStatus]=useState('all');
  useEffect(()=>{const controller=new AbortController();setError(false);setLoading(true);setItems([]);void(async()=>{let cursor:string|undefined;const found=new Map<string,HrPosition>();const seen=new Set<string>();do{
    const page=await api.listPositions({limit:100,cursor},controller.signal);for(const item of page.items)found.set(item.positionId,item);cursor=page.nextCursor??undefined;if(cursor&&seen.has(cursor))throw new Error();if(cursor)seen.add(cursor);
  }while(cursor);if(!controller.signal.aborted)setItems([...found.values()]);})().catch(()=>{if(!controller.signal.aborted)setError(true);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();},[api,attempt]);
  const visible=items.filter(item=>(status==='all'||item.internalStatus===status)&&[item.title,item.officialJobId,item.department,...item.locations].some(value=>value?.toLowerCase().includes(query.trim().toLowerCase())));
  return <main className="hr-position-directory"><div className="hr-position-page-inner"><header className="hr-pw-heading"><div><span>RECRUITMENT WORKSPACE</span><h1>岗位工作台</h1><p>围绕一个岗位，查看要求、候选人、面试与主对话中保存的成果。</p></div><BriefcaseBusiness size={34} aria-hidden="true"/></header>
    <div className="hr-pw-directory-tools"><label><Search size={18}/><input aria-label="搜索岗位" placeholder="搜索岗位、编号、部门或地点" type="search" value={query} onChange={event=>setQuery(event.target.value)}/></label><select aria-label="岗位状态" value={status} onChange={event=>setStatus(event.target.value)}><option value="all">全部岗位</option><option value="active">进行中</option><option value="draft">草案</option><option value="archived">已归档</option></select><span>{loading?'读取中':`${visible.length} 个岗位`}</span></div>
    {error?<p className="hr-pw-empty" role="alert">岗位暂时无法读取。<button onClick={()=>setAttempt(value=>value+1)}>重新读取</button></p>:loading?<p className="hr-pw-empty" role="status">正在读取岗位…</p>:<div className="hr-pw-position-grid">{visible.map(item=><PlatformLink className="hr-pw-position-card" key={item.positionId} href={`/hr/positions/${encodeURIComponent(item.positionId)}`}><div><span>{item.officialJobId??'自建岗位'}</span><em>{item.internalStatus==='archived'?'已归档':item.internalStatus==='draft'?'草案':'进行中'}</em></div><h2>{item.title}</h2><p>{[item.department,...item.locations].filter(Boolean).join(' · ')||'部门与地点待补充'}</p><div className="hr-pw-card-flow"><span>JD / JR</span><span>候选人</span><span>面试</span><span>复盘</span></div><footer>查看岗位工作流 <ArrowUpRight size={16}/></footer></PlatformLink>)}</div>}
    {!loading&&!error&&!visible.length&&<p className="hr-pw-empty">没有匹配的岗位，试试其他搜索条件。</p>}
  </div></main>;
}
