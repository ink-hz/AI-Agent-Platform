import { useEffect, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type { HrContextVersion } from "../../hrR12Types";
import { MessageMarkdown } from "../../components/MessageMarkdown";

const LABELS: Record<string,string> = { mission:"岗位使命", jd:"JD · 岗位职责", jr:"JR · 任职要求", competencies:"能力要求", profile:"人才画像", talent_profile:"人才画像", sourcing:"搜寻策略", sourcing_strategy:"搜寻策略", interview_standard:"面试标准", unknowns:"待澄清事项" };
function text(value: Record<string,unknown>) {
  const valueText=[value.markdown,value.visible_markdown,value.summary,value.text].find(item=>typeof item==='string');
  return typeof valueText==='string'?valueText:JSON.stringify(value,null,2);
}
export function HrContextVersionView({version}:{version:HrContextVersion}) {
  return <article><h3>v{version.displayVersion}{version.confirmedAt?` · ${new Date(version.confirmedAt).toLocaleString()}`:''}</h3>
    <p>{version.summary}</p>{Object.entries(version.modules).map(([key,value])=><section key={key}><h4>{LABELS[key]??key}</h4><MessageMarkdown content={text(value)}/></section>)}</article>;
}
export function HrPositionContextPanel({api,positionId,refreshGeneration=0,heading="已确认岗位标准"}:{
  api:Pick<HrR12Api,"context">;positionId:string;refreshGeneration?:number;heading?:string;
  readOnly?:boolean;onConfirmed?:(context:HrContextVersion)=>void;
}) {
  const [data,setData]=useState<{current:HrContextVersion|null;history:HrContextVersion[]}|null>(null);
  const [error,setError]=useState(false);const [retry,setRetry]=useState(0);
  useEffect(()=>{const controller=new AbortController();setData(null);setError(false);
    api.context(positionId,controller.signal).then(value=>{if(!controller.signal.aborted)setData(value);})
      .catch(()=>{if(!controller.signal.aborted)setError(true);});return()=>controller.abort();
  },[api,positionId,refreshGeneration,retry]);
  return <section aria-label={heading} className="hr-r12-panel hr-context-panel"><h2>{heading}</h2>
    {error?<p role="alert">岗位标准读取失败。<button onClick={()=>setRetry(value=>value+1)}>重试</button></p>:!data?<p>正在读取…</p>:<>
      {data.current?<HrContextVersionView version={data.current}/>:<p>尚无已确认标准。在主对话中讨论要求，逐项确认后会保存在这里。</p>}
      <details><summary>历史确认版本</summary>{data.history.filter(version=>version.status==='superseded').map(version=><HrContextVersionView key={version.contextVersionId} version={version}/>)}</details>
    </>}
  </section>;
}
