import type { MouseEvent } from 'react';
import { platformPath, type Account } from '../auth';
import { useDeploymentContext } from '../deploymentContext';
import { actionPath } from '../panoramaNavigation';
import type { PanoramaActionId } from '../panoramaTypes';

const COMMON: [PanoramaActionId, string][] = [
  ['brain', 'Agent 大脑'], ['agents', '专业 Agent'], ['missions', '历史任务'], ['notes', '工程笔记'], ['account', '我的账号'],
];
const MANAGEMENT: [PanoramaActionId, string][] = [
  ['operations', '运行总览'], ['agent-admin', 'Agent 管理'], ['sessions', 'Session'], ['review', '复审闭环'],
  ['activity', '运行记录'], ['identity', '身份管理'], ['governance', '治理审计'], ['access', '访问记录'],
];
const APPLICATIONS: [PanoramaActionId, string][] = [['hr', 'HR'], ['office', '行政服务'], ['voc', 'VOC'], ['fae', 'FAE']];
function plainClick(event: MouseEvent<HTMLAnchorElement>) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

export function PlatformFunctions({ account, onAction, onOpenPath }: {
  account: Account; onAction: (action: PanoramaActionId) => void; onOpenPath: (path: string, external: boolean) => void;
}) {
  const { deployment, resolved } = useDeploymentContext();
  if (account.role !== 'platform_admin' && account.role !== 'platform_owner') return null;
  const owner = account.role === 'platform_owner';
  const cloud = deployment?.mode === 'cloud-replica' && deployment.read_only;
  const management = MANAGEMENT.filter(([id]) => (id !== 'access' || owner) && (id !== 'review' || (resolved && !cloud)));
  const actionLink = ([id, label]: [PanoramaActionId, string]) => {
    const target = actionPath(id);
    return <a key={id} href={platformPath(target.path)} onClick={event => {
      if (!plainClick(event)) return;
      event.preventDefault(); onAction(id);
    }}>{label}{target.external && <span aria-label="独立应用"> ↗</span>}</a>;
  };
  const pathLink = (label: string, path: string, external: boolean) => <a href={platformPath(path)} onClick={event => {
    if (!plainClick(event)) return;
    event.preventDefault(); onOpenPath(path, external);
  }}>{label}{external && <span aria-label="独立应用"> ↗</span>}</a>;
  return <nav className="platform-functions" aria-label="平台功能">
    <header><strong>Orbbec Agent Platform</strong><span>平台功能</span></header>
    <div className="platform-functions__groups">
      <section><h2>通用功能</h2><div>{COMMON.map(actionLink)}</div></section>
      <section><h2>平台管理</h2><div>{management.map(actionLink)}</div></section>
      <section><h2>业务应用</h2><div>
        {APPLICATIONS.map(actionLink)}
        {(owner || account.workspace_scopes.includes('fae_workbench')) && pathLink('FAE 工作台', '/fae/manage/', false)}
        {pathLink('VOC 管理', '/voc/manage/', true)}
      </div></section>
    </div>
  </nav>;
}
