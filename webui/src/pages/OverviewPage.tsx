import { useEffect, useState } from 'react';
import { PlatformPageHeader } from '../platform/PlatformPageHeader';
import { runTimedPollingCycle, startPolling } from '../dashboard';
import { fetchRuntimeDashboard } from '../runtimeDashboardApi';
import { RuntimeDashboard } from '../runtimeDashboardUi';
import type { DashboardDays, DashboardOverview } from '../runtimeDashboardTypes';
import '../runtimeDashboard.css';

// A window/identity change creates a new session synchronously, so a previous
// account or 7-day snapshot is never rendered under the next request's label.
export function OverviewPage({identityKey = 'authenticated-dashboard'}: {identityKey?:string}) {
  const [days,setDays]=useState<DashboardDays>(7);
  return <div className="runtime-dashboard">
    <PlatformPageHeader title="运行中心" description="当前业务应用、运行观测与可追溯使用数据。" actions={<label className="runtime-window">统计窗口 <select value={days} onChange={event=>setDays(Number(event.target.value) as DashboardDays)}><option value={7}>近 7 天</option><option value={30}>近 30 天</option></select></label>}/>
    <DashboardSession key={JSON.stringify([identityKey,days])} days={days}/>
  </div>;
}
function DashboardSession({days}: {days:DashboardDays}) {
  const [data,setData]=useState<DashboardOverview|null>(null);
  const [failed,setFailed]=useState(false);
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    let disposed=false;
    let active:AbortController|null=null;
    const stop=startPolling(async()=>{
      const controller=new AbortController();active=controller;
      await runTimedPollingCycle({controller,request:signal=>fetchRuntimeDashboard(days,signal),isDisposed:()=>disposed,
        onSuccess:value=>{setData(value);setFailed(false);},onFailure:()=>setFailed(true),timeoutMs:5000});
      if(active===controller) active=null;
    },30_000);
    return ()=>{disposed=true;active?.abort();stop();};
  },[days,attempt]);
  return <>
    {failed && <div className="banner error-banner" role="alert">{data ? '刷新失败 · 当前显示上次快照，数据已过期。' : '运行看板读取失败，数据与运行状态未知。'}<button type="button" onClick={()=>setAttempt(value=>value+1)}>重试</button></div>}
    {data ? <RuntimeDashboard data={data}/> : !failed && <p role="status">正在读取运行看板…</p>}
  </>;
}
