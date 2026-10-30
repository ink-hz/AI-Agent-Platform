import { useWebCatalog } from "./WebCatalog";
import { webEntryPath } from "./webEntryPolicy";
import { BUSINESS_AGENT_LABELS } from "./agentLabels";
import type { AgentCapabilityCard } from "../brainTypes";
import { useEffect, useState, type MouseEvent } from 'react';
import { Activity, ChevronDown, ChevronRight, BookOpen, Boxes, Code2, ExternalLink, Headset, Home, MessagesSquare, ScanEye, UsersRound, Building2, type LucideIcon } from 'lucide-react';
import { platformPath, type Account } from '../auth';
import { navigate, type Route } from '../router';
import { SDK_ADMIN_SECTIONS } from '../sdk-admin/sdkAdminNavigation';
import { manufacturingAccess } from "../manufacturingApi";
import { sdkAdminClient } from '../sdk-admin/sdkAdminApi';

interface Item { label: string; path: string; icon: LucideIcon; external?: boolean; newTab?: boolean; routes: readonly string[] }
interface Group { label: string; items: Item[]; hideHeading?: boolean }
const aiWork: Item[] = [
  { label:'Agent 目录', path:'/agents', icon:Boxes, routes:['agents','marketing','marketing-conversation'] },
];
const runtime: Item[] = [
  { label:'运行中心', path:'/admin', icon:Activity, routes:['admin-overview','admin-agents','admin-agent','admin-agent-runtime','admin-activity'] },
  { label:'会话记录', path:'/admin/sessions', icon:MessagesSquare, routes:['admin-sessions','admin-session'] },
  { label:'任务复审', path:'/admin/review', icon:ScanEye, routes:['admin-review'] },
];
function groupsFor(account?: Account | null, readOnly = false, sdkAvailable = false, manufacturingAvailable = false, applications: AgentCapabilityCard[] = []): Group[] {
  const manager = account?.role === 'platform_owner' || account?.role === 'platform_admin';
  const faeManager = account?.role === 'platform_owner' || account?.workspace_scopes.includes('fae_workbench');
  const vocManager = manager || account?.role === 'management_viewer';
  const groups: Group[] = [
    { label:'公司全景', items:[
      {label:'业务布局',path:'/',icon:Home,routes:['home','ai-engineering']},
      {label:'组织架构',path:'/organization',icon:Building2,routes:['organization']},
    ] },
    { label:'AI 工作', items:manager ? [...aiWork, {label:'Agent 设计',path:'/admin/agent-designs',icon:BookOpen,routes:['admin-agent-designs']}] : aiWork },
    { label:'业务工作台', items:[
      ...(manufacturingAvailable ? [{label:'大制造',path:'/manufacturing',icon:Boxes,routes:['manufacturing']}] : []),
      ...(sdkAvailable ? [{ label:'SDK 开发管理', path:'/sdk-admin', icon:Code2, routes:['sdk-admin'] }] : []),
      ...applications.map(card => ({label: card.agent_id === 'ai-fae-agent' ? '技术支持咨询' : card.agent_id === 'ai-daq-fae-agent' ? 'DAQ 技术支持咨询' : card.agent_id === 'hr-bot' ? '人力资源' : card.agent_id === 'ai-admin-agent' ? '行政服务' : BUSINESS_AGENT_LABELS[card.agent_id] ?? card.display_name,
        path:webEntryPath(card)!,icon:card.agent_id.includes('fae') ? Headset : Building2,external:true,routes:[]})),
      ...(faeManager ? [{label:'技术支持管理',path:'/fae/manage/',icon:Headset,routes:['fae-manage-overview','fae-manage-sessions','fae-manage-session','fae-manage-issues','fae-manage-issue','fae-manage-reports','fae-manage-report']}] : []),
      ...(vocManager ? [{label:'客户洞察管理',path:'/admin/voc',icon:MessagesSquare,routes:['admin-voc']}] : []),
    ] },
  ];
  if (manager) {
    groups.push({label:'运行中心', items:runtime.filter(item => !readOnly || item.path !== '/admin/review')});
    groups.push({label:'平台管理', items:[
      {label:'账号与权限',path:'/admin/identity',icon:UsersRound,routes:['admin-identity','admin-governance']},
      ...(account?.role === 'platform_owner' ? [{label:'访问记录',path:'/admin/access',icon:ScanEye,routes:['admin-access']}] : []),
    ]});
  }
  groups.push({label:'辅助入口',hideHeading:true,items:[{label:'工程笔记',path:'/ai-notes',icon:BookOpen,external:true,newTab:true,routes:['ai-notes','ai-note']}]});
  return groups;
}
function follow(event: MouseEvent<HTMLAnchorElement>, item: Item, panorama = false) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  // Native document links use the existing beforeunload protection exactly once.
  if (item.external) return;
  event.preventDefault();
  navigate(item.path, panorama ? {state: {panorama: true}} : {});
}
export function PlatformSidebar({route,account,readOnly,collapsed,panorama = false}: {route:Route;account?:Account|null;readOnly:boolean;collapsed:boolean;panorama?:boolean}) {
  const catalog = useWebCatalog();
  const [manufacturingAvailable, setManufacturingAvailable] = useState(false);
  useEffect(() => {
    setManufacturingAvailable(false);
    if (!account) return;
    let live = true;
    const controller = new AbortController();
    void manufacturingAccess(controller.signal).then(access => {
      if (live) setManufacturingAvailable(access.allowed && access.available);
    }).catch(() => { if (live) setManufacturingAvailable(false); });
    return () => { live = false; controller.abort(); };
  }, [account?.internal_user_id, account?.role]);
  const [sdkAvailable, setSdkAvailable] = useState(false);
  const [sdkExpanded, setSdkExpanded] = useState(route.name === 'sdk-admin');
  useEffect(() => {
    if (route.name === 'sdk-admin') setSdkExpanded(true);
  }, [route]);
  useEffect(() => {
    if (!account) { setSdkAvailable(false); return; }
    let live = true;
    void sdkAdminClient.access().then(access => { if (live) setSdkAvailable(access.allowed); })
      .catch(() => { if (live) setSdkAvailable(false); });
    return () => { live = false; };
  }, [account?.internal_user_id]);
  return <aside id="platform-navigation" className="platform-sidebar" hidden={collapsed}>
    <nav aria-label="主导航">
      {groupsFor(account,readOnly,sdkAvailable,manufacturingAvailable,catalog?.applications).map(group => <section className="platform-nav-group" key={group.label} aria-label={group.label}>
        {!group.hideHeading && <h2>{group.label}</h2>}
        {group.items.map(item => {
          if (item.path === '/sdk-admin') return <div className="platform-sdk-navigation" key={item.path}>
            <div className="platform-sdk-heading">
              <a href={platformPath(item.path)} className={route.name === 'sdk-admin' ? 'is-workspace-current' : undefined} onClick={event => follow(event,item,panorama)}>
                <Code2 size={17} aria-hidden="true"/><span>{item.label}</span>
              </a>
              <button type="button" aria-controls="sdk-workspace-navigation" aria-expanded={sdkExpanded}
                aria-label={sdkExpanded ? '收起 SDK 工作区' : '展开 SDK 工作区'} onClick={() => setSdkExpanded(value => !value)}>
                {sdkExpanded ? <ChevronDown size={15} aria-hidden="true"/> : <ChevronRight size={15} aria-hidden="true"/>}
              </button>
            </div>
            <div id="sdk-workspace-navigation" className="platform-sdk-children" role="group" aria-label="SDK 工作区" hidden={!sdkExpanded}>
              {SDK_ADMIN_SECTIONS.map(({id,path,title,icon:Icon}) => {
                const current = route.name === 'sdk-admin' && route.section === id;
                return <a key={id} href={platformPath(path)} className={current ? 'is-current' : undefined} aria-current={current ? 'page' : undefined}
                  onClick={event => follow(event,{path,label:title,icon:Icon,routes:[]},panorama)}>
                  <Icon size={15} aria-hidden="true"/><span>{title}</span>
                </a>;
              })}
            </div>
          </div>;
          const current = item.routes.includes(route.name)
            || (route.name === 'admin-permissions' && item.path === '/admin/identity');
          const Icon=item.icon;
          return <a key={item.path} href={item.newTab ? platformPath(item.path) : item.external ? item.path : platformPath(item.path)}
            target={item.newTab ? '_blank' : undefined} rel={item.newTab ? 'noopener noreferrer' : undefined}
            className={current ? 'is-current' : undefined} aria-current={current ? 'page' : undefined}
            onClick={event => follow(event,item,panorama)}>
            <Icon size={17} aria-hidden="true"/><span>{item.label}</span>
            {item.external && <ExternalLink className="platform-nav-external" size={12} aria-hidden="true"/>}
          </a>;
        })}
      </section>)}
    </nav>
  </aside>;
}
