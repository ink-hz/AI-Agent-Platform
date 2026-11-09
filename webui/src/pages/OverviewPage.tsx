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
  const [refreshKey,setRefreshKey]=useState(0);
  return <div className="runtime-dashboard">
    <PlatformPageHeader title="运行中心" description="查看业务 Agent 的运行状态、真实使用情况和工作成果。" actions={<div className="runtime-home-actions"><label className="runtime-window">统计窗口 <select value={days} onChange={event=>setDays(Number(event.target.value) as DashboardDays)}><option value={7}>近 7 天</option><option value={30}>近 30 天</option></select></label><button type="button" onClick={()=>setRefreshKey(value=>value+1)}>立即刷新</button></div>}/>
    <DashboardSession key={JSON.stringify([identityKey,days])} days={days} refreshKey={refreshKey}/>
  </div>;
}
function DashboardSession({days,refreshKey}: {days:DashboardDays;refreshKey:number}) {
  const [data,setData]=useState<DashboardOverview|null>(null);
  const [failed,setFailed]=useState(false);
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    let disposed=false;
    let active:AbortController|null=null;
    let generation=0;
    let stop=()=>{};
    const restart=()=>{
      const current=++generation;
      stop();active?.abort();active=null;
      if(disposed || document.visibilityState==='hidden') return;
      stop=startPolling(async()=>{
        const controller=new AbortController();active=controller;
        await runTimedPollingCycle({controller,request:signal=>fetchRuntimeDashboard(days,signal),isDisposed:()=>disposed || current!==generation,
          onSuccess:value=>{setData(value);setFailed(false);},onFailure:()=>setFailed(true),timeoutMs:5000});
        if(active===controller) active=null;
      },10_000);
    };
    restart();
    document.addEventListener('visibilitychange',restart);
    window.addEventListener('online',restart);
    return ()=>{disposed=true;++generation;active?.abort();stop();document.removeEventListener('visibilitychange',restart);window.removeEventListener('online',restart);};
  },[days,attempt,refreshKey]);
  return <>
    {failed && <div className="runtime-reconnecting" role="status">{data ? '正在重新连接，暂时显示上次成功读取的数据。' : '运行看板读取失败，正在重新连接。'}<button type="button" onClick={()=>setAttempt(value=>value+1)}>立即重试</button></div>}
    {data ? <RuntimeDashboard data={data}/> : !failed && <p role="status">正在读取运行看板…</p>}
  </>;
}
