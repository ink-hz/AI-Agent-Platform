import { Component, useEffect, useState, type ReactNode } from 'react';
import type { Route } from './router';
import { workspaceGroup } from './panoramaNavigation';
import { WorkspaceDraftCommit } from "./workspaceDraft";
import { routeDocumentTitle } from './documentTitle';

class WorkspaceBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
 state={failed:false};
 static getDerivedStateFromError() { return {failed:true}; }
 render() { return this.state.failed ? <div role="alert"><h2>工作区暂时不可用</h2><p>当前业务页面未能打开，可以回到全景后重试。未确认的操作结果请在原任务中核实。</p><button onClick={()=>this.setState({failed:false})}>重试工作区</button></div> : this.props.children; }
}
export function PanoramaWorkArea({route,renderWorkspace,onClose,onDirty}: {
 route?: Route; renderWorkspace:(route:Route)=>ReactNode; onClose:()=>void; onDirty:(dirty:boolean)=>void;
}) {
 const [saved,setSaved]=useState<Route|undefined>(route);
 useEffect(()=>{if(route) setSaved(route);},[route]);
 useEffect(()=>{
   if(!route) return;
   const close=(event:KeyboardEvent)=>{if(event.key==='Escape' && !event.defaultPrevented) onClose();};
   window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);
 },[route,onClose]);
 const retained=route??saved;
 if(!retained) return null;
 return <section className="panorama-work-area" hidden={!route} aria-label="图内工作区" onInputCapture={()=>onDirty(true)}>
   <header className="panorama-work-area-heading"><div><span>AI 工程全景 / 共用能力</span><h2>{routeDocumentTitle(retained).split(' · ')[0]}</h2></div><button type="button" onClick={onClose}>回到全景</button></header>
   <div className="panorama-work-area-body"><WorkspaceBoundary key={workspaceGroup(retained)}><WorkspaceDraftCommit.Provider value={() => onDirty(false)}>{renderWorkspace(retained)}</WorkspaceDraftCommit.Provider></WorkspaceBoundary></div>
 </section>;
}
