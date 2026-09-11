import { useEffect, useRef, useState } from 'react';
import { platformPath } from '../../auth';
import { HrLegacyPanoramaWorkspace } from './HrLegacyPanoramaWorkspace';
import { HrResearchWorkspace } from './HrResearchWorkspace';
import { HrSourceWorkspace } from './HrSourceWorkspace';
import { INTELLIGENCE_BEFORE_NAVIGATION, intelligenceScroller, navigateIntelligence, type IntelligenceNavigationDetail } from './hrIntelligenceNavigation';
import type { SourceCatalog } from './hrSourceTypes';
import './hrSources.css';
export function HrPanoramaWorkspace(props: Parameters<typeof HrLegacyPanoramaWorkspace>[0]) {
  const [search, setSearch] = useState(() => window.location.search);
  const [sourceCatalog, setSourceCatalog] = useState<SourceCatalog | null>(null);
  const identity = `${props.account.internal_user_id}:${props.account.csrf_token}`;
  useEffect(() => {setSourceCatalog(null);}, [identity]);
  const shell = useRef<HTMLDivElement>(null);
  const scroll = useRef({sources:0,research:0});
  const restore = useRef<number | null>(null);
  const renderedSearch = useRef(search);
  renderedSearch.current=search;
  useEffect(() => {
    const layerOf = (value:string) => {
      const query=new URLSearchParams(value);
      return query.get('layer')==='sources' ? 'sources' : query.get('layer')==='research' || query.has('research') || query.has('edition') ? 'research' : 'sources';
    };
    const capture = (previousSearch:string,nextSearch:string) => {
      const previousLayer=layerOf(previousSearch); const nextLayer=layerOf(nextSearch);
      if(previousLayer===nextLayer)return;
      scroll.current[previousLayer]=intelligenceScroller(shell.current).scrollTop;
      const previous=new URLSearchParams(previousSearch); const next=new URLSearchParams(nextSearch);
      const sourceJump=nextLayer==='sources'&&['source_edition','research_company','source_job'].some(key=>previous.get(key)!==next.get(key));
      restore.current=sourceJump?0:scroll.current[nextLayer];
    };
    const sync = () => setSearch(window.location.search);
    const popstate = () => {capture(renderedSearch.current,window.location.search);sync();};
    const beforeNavigation = (event:Event) => {const {previousSearch,nextSearch}=(event as CustomEvent<IntelligenceNavigationDetail>).detail;capture(previousSearch,nextSearch);};
    const ready = () => {
      if (restore.current === null) return;
      const top = restore.current; restore.current = null;
      requestAnimationFrame(() => {intelligenceScroller(shell.current).scrollTop=top;});
    };
    window.addEventListener('popstate', popstate); window.addEventListener('platform:navigate', sync); window.addEventListener(INTELLIGENCE_BEFORE_NAVIGATION, beforeNavigation); window.addEventListener('hr:intelligence-ready', ready);
    return () => {window.removeEventListener('popstate', popstate); window.removeEventListener('platform:navigate', sync); window.removeEventListener(INTELLIGENCE_BEFORE_NAVIGATION, beforeNavigation); window.removeEventListener('hr:intelligence-ready', ready);};
  }, []);
  const query = new URLSearchParams(search);
  const archive = ['archive','topics'].includes(query.get('view')??'') || ['company','topic','bundle_id'].some(key=>query.has(key));
  const layer = query.get('layer')==='sources' ? 'sources' : query.get('layer')==='research' || query.has('research') || query.has('edition') ? 'research' : 'sources';
  const [visitedResearch,setVisitedResearch]=useState(layer==='research');
  useEffect(()=>{if(layer==='research')setVisitedResearch(true);},[layer]);
  const changeLayer = (next:'sources'|'research') => {
    if (next===layer) return;
    navigateIntelligence({layer:next});
  };
  if (archive) return <><div className="hr-research-archive-banner">历史情报归档 · 保留原发布内容与已有引用 <a href={platformPath('/hr/panorama')}>返回情报资料 →</a></div><HrLegacyPanoramaWorkspace {...props}/></>;
  return <div ref={shell} className="hr-intelligence-layers">
    <nav className="hr-intelligence-layer-nav" aria-label="情报内容层级"><button aria-current={layer==='sources'?'page':undefined} onClick={()=>changeLayer('sources')}>原始资料</button><button aria-current={layer==='research'?'page':undefined} onClick={()=>changeLayer('research')}>AI 分析报告</button><span>采集事实与分析判断，分层阅读。</span></nav>
    <div data-layer="sources" hidden={layer!=='sources'}><HrSourceWorkspace key={identity} account={props.account} search={search} active={layer==='sources'} onCatalog={setSourceCatalog}/></div>
    <div data-layer="research" hidden={layer!=='research'}>{(visitedResearch||layer==='research')&&<HrResearchWorkspace key={identity} account={props.account} active={layer==='research'} companyOptions={sourceCatalog?.companies.map(c=>({id:c.company_key,name:c.name}))}/>}</div>
  </div>;
}
