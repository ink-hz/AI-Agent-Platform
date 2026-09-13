import { useEffect, useMemo, useState } from "react";
import type { Account } from "../../auth";
import { createHrApi, type HrApi } from "../../hrApi";
import type { HrPosition } from "../../hrTypes";
export function HrPositionIndex({account,api:injectedApi,onSelect}:{account:Account;api?:HrApi;onSelect?:(position:HrPosition)=>void}){
  const api=useMemo(()=>injectedApi??createHrApi(account.csrf_token),[injectedApi,account.csrf_token]);
  const [items,setItems]=useState<HrPosition[]>([]);const [query,setQuery]=useState('');const [error,setError]=useState(false);const [attempt,setAttempt]=useState(0);
  useEffect(()=>{const controller=new AbortController();setError(false);void(async()=>{let cursor:string|undefined;const found=new Map<string,HrPosition>();const seen=new Set<string>();do{
    const page=await api.listPositions({limit:100,cursor},controller.signal);for(const item of page.items)found.set(item.positionId,item);cursor=page.nextCursor??undefined;if(cursor&&seen.has(cursor))throw new Error();if(cursor)seen.add(cursor);
  }while(cursor);if(!controller.signal.aborted)setItems([...found.values()]);})().catch(()=>{if(!controller.signal.aborted)setError(true);});return()=>controller.abort();},[api,attempt]);
  const visible=items.filter(item=>[item.title,item.officialJobId,item.department,...item.locations].some(value=>value?.toLowerCase().includes(query.trim().toLowerCase())));
  return <main className="hr-position-index"><header><h1>岗位资料</h1><p>选择岗位，在主对话中继续招聘协作。</p></header><label>搜索岗位<input type="search" value={query} onChange={event=>setQuery(event.target.value)}/></label>
    {error?<p role="alert">岗位暂时无法读取。<button onClick={()=>setAttempt(value=>value+1)}>重试</button></p>:<div className="hr-position-list">{visible.map(item=><button className="hr-position-picker-option" key={item.positionId} disabled={item.internalStatus!=='active'} onClick={()=>onSelect?.(item)}><strong>{item.title}</strong><small>{[item.officialJobId,item.department,...item.locations].filter(Boolean).join(' · ')}</small></button>)}</div>}
  </main>;
}
