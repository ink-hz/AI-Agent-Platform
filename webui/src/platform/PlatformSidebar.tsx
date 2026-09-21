import type { MouseEvent } from 'react';
import { Activity, BookOpen, Bot, Boxes, ClipboardList, ExternalLink, Headset, Home, ListChecks, MessageSquare, MessagesSquare, ScanEye, ShieldCheck, UsersRound, Building2, KeyRound, type LucideIcon } from 'lucide-react';
import { platformPath, type Account } from '../auth';
import { navigate, type Route } from '../router';

interface Item { label: string; path: string; icon: LucideIcon; external?: boolean; routes: readonly string[] }
interface Group { label: string; items: Item[]; hideHeading?: boolean }
const aiWork: Item[] = [
  { label:'AI 助手', path:'/brain', icon:MessageSquare, routes:['brain','conversation','conversations'] },
  { label:'Agent 目录', path:'/agents', icon:Boxes, routes:['agents','marketing','marketing-conversation'] },
  { label:'历史任务', path:'/missions', icon:ClipboardList, routes:['missions','mission'] },
];
const runtime: Item[] = [
  { label:'运行概览', path:'/admin', icon:Activity, routes:['admin-overview'] },
  { label:'Agent 状态', path:'/admin/agents', icon:Bot, routes:['admin-agents','admin-agent','admin-agent-runtime'] },
  { label:'会话记录', path:'/admin/sessions', icon:MessagesSquare, routes:['admin-sessions','admin-session'] },
  { label:'运行事件', path:'/admin/activity', icon:ListChecks, routes:['admin-activity'] },
  { label:'任务复审', path:'/admin/review', icon:ScanEye, routes:['admin-review'] },
];
function groupsFor(account?: Account | null, readOnly = false): Group[] {
  const manager = account?.role === 'platform_owner' || account?.role === 'platform_admin';
  const faeManager = account?.role === 'platform_owner' || account?.workspace_scopes.includes('fae_workbench');
  const vocManager = manager || account?.role === 'management_viewer';
  const groups: Group[] = [
    { label:'公司全景', items:[
      {label:'业务布局',path:'/',icon:Home,routes:['home','ai-engineering']},
      {label:'组织架构',path:'/organization',icon:Building2,routes:['organization']},
    ] },
    { label:'AI 工作', items:aiWork },
    { label:'业务工作台', items:[
      { label:'技术支持', path:faeManager ? '/fae/manage/' : '/fae/', icon:Headset, external:!faeManager, routes:['fae-manage-overview','fae-manage-sessions','fae-manage-session','fae-manage-issues','fae-manage-issue','fae-manage-reports','fae-manage-report'] },
      { label:'人力资源', path:'/hr/', icon:UsersRound, external:true, routes:[] },
      { label:'行政服务', path:'/office/', icon:Building2, external:true, routes:[] },
      { label:'客户洞察', path:vocManager ? '/admin/voc' : '/voc/', icon:MessagesSquare, external:!vocManager, routes:['admin-voc'] },
    ] },
  ];
  if (manager) {
    groups.push({label:'运行中心', items:runtime.filter(item => !readOnly || item.path !== '/admin/review')});
    groups.push({label:'平台管理', items:[
      {label:'账号与权限',path:'/admin/identity',icon:UsersRound,routes:['admin-identity']},
      {label:'审计日志',path:'/admin/governance',icon:ShieldCheck,routes:['admin-governance']},
      ...(account?.role === 'platform_owner' ? [{label:'访问记录',path:'/admin/access',icon:ScanEye,routes:['admin-access']}] : []),
    ]});
  }
  groups.push({label:'辅助入口',hideHeading:true,items:[{label:'工程笔记',path:'/ai-notes',icon:BookOpen,routes:['ai-notes','ai-note']}]});
  return groups;
}
function follow(event: MouseEvent<HTMLAnchorElement>, item: Item) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  // Native document links use the existing beforeunload protection exactly once.
  if (item.external) return;
  event.preventDefault();
  navigate(item.path);
}
export function PlatformSidebar({route,account,readOnly,collapsed}: {route:Route;account?:Account|null;readOnly:boolean;collapsed:boolean}) {
  return <aside id="platform-navigation" className="platform-sidebar" hidden={collapsed}>
    <nav aria-label="主导航">
      {groupsFor(account,readOnly).map(group => <section className="platform-nav-group" key={group.label} aria-label={group.label}>
        {!group.hideHeading && <h2>{group.label}</h2>}
        {group.items.map(item => {
          const permissionSection = route.name === 'admin-permissions' ? route.section : null;
          const current=item.routes.includes(route.name) || (permissionSection === 'fae' && item.label === '技术支持')
            || (permissionSection === 'voc' && item.label === '客户洞察')
            || ((permissionSection === 'observers' || permissionSection === 'partners') && item.path === '/admin/identity');
          const accessPath = account?.role === 'platform_owner'
            ? item.label === '技术支持' ? '/fae/manage/access' : item.label === '客户洞察' ? '/admin/voc/access' : null
            : null;
          const Icon=item.icon;
          return <div key={item.path} className="platform-nav-item"><a href={item.external ? item.path : platformPath(item.path)}
            className={current ? 'is-current' : undefined} aria-current={current ? 'page' : undefined}
            onClick={event => follow(event,item)}>
            <Icon size={17} aria-hidden="true"/><span>{item.label}</span>
            {item.external && <ExternalLink className="platform-nav-external" size={12} aria-hidden="true"/>}
          </a>{accessPath && <a className="platform-nav-permission" href={platformPath(accessPath)}
            aria-label={`${item.label}权限`} title={`${item.label}权限`}
            onClick={event => follow(event, {label: `${item.label}权限`, path: accessPath, icon: KeyRound, routes: []})}>
            <KeyRound size={14} aria-hidden="true" />
          </a>}</div>;
        })}
      </section>)}
    </nav>
  </aside>;
}
