import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { AiEngineeringApiError } from '../aiEngineeringApi';
import { fetchDepartment, fetchOrganization, type Department, type DepartmentDetail, type OrganizationTree } from './organizationApi';
import './organization.css';

interface Props { active: boolean; onAuthorizationFailure: (error: AiEngineeringApiError) => void; onOpen?: () => void }
const updatedTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const fullUpdatedTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'full', timeStyle: 'short' });
// Colors distinguish real branches, not inferred business functions.
const branchColors = [
  ['#346bb4', '#dceaff', '#f0f5ff', '#b4cbee', '#24496f'],
  ['#287d70', '#d5eee6', '#edf7f3', '#abd4c8', '#225b51'],
  ['#7b58aa', '#e9e0f7', '#f5f0fb', '#ccbbe4', '#554075'],
  ['#ad7025', '#f9e8c9', '#fcf5e8', '#e3ca98', '#78501e'],
  ['#aa5275', '#f5dfE8', '#fbf0f5', '#e1b7c9', '#763b55'],
  ['#327d97', '#d9edf4', '#eef7fa', '#acd0df', '#295c70'],
];
function branchStyle(id: string): CSSProperties {
  let hash = 0;
  for (const char of id) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) >>> 0;
  const [accent, fill, surface, line, ink] = branchColors[hash % branchColors.length];
  return { '--org-accent': accent, '--org-fill': fill, '--org-surface': surface, '--org-line': line, '--org-ink': ink } as CSSProperties;
}
export function OrganizationLayout({ active, onAuthorizationFailure, onOpen }: Props) {
  const [tree, setTree] = useState<OrganizationTree | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<DepartmentDetail | null>(null);
  const [treeLoading, setTreeLoading] = useState(false); const [detailLoading, setDetailLoading] = useState(false);
  const [treeError, setTreeError] = useState(false); const [detailError, setDetailError] = useState(false);
  const [notice, setNotice] = useState(''); const [revision, setRevision] = useState(0);
  const treeRequest = useRef<AbortController | null>(null); const detailRequest = useRef<AbortController | null>(null);
  const previousRoot = useRef<string | null>(null);
  const panelRef = useRef<HTMLElement | null>(null); const triggerRef = useRef<HTMLButtonElement | null>(null);
  const authorized = useRef(true); const authCallback = useRef(onAuthorizationFailure); authCallback.current = onAuthorizationFailure;
  function clearDetail(restoreFocus = false) { if (restoreFocus) triggerRef.current?.focus(); detailRequest.current?.abort(); setSelected(null); setDetail(null); setDetailError(false); setDetailLoading(false); }
  function authorizationError(error: unknown): boolean {
    if (!(error instanceof AiEngineeringApiError) || (error.status !== 401 && error.status !== 403)) return false;
    authorized.current = false; treeRequest.current?.abort(); clearDetail(); setTree(null); setExpanded(new Set()); setTreeLoading(false); setTreeError(false); setNotice(''); authCallback.current(error); return true;
  }
  useEffect(() => {
    clearDetail();
    if (!active) { setTreeLoading(false); return; }
    authorized.current = true; const controller = new AbortController(); treeRequest.current = controller;
    setTreeError(false); setTreeLoading(true);
    fetchOrganization(controller.signal).then(value => {
      if (controller.signal.aborted || !authorized.current) return;
      const sameRoot = previousRoot.current === value.root_id;
      const ids = new Set(value.departments.map(department => department.id));
      setTree(value);
      setExpanded(previous => sameRoot ? new Set([...previous].filter(id => ids.has(id))) : new Set([value.root_id]));
      previousRoot.current = value.root_id;
    }).catch(error => {
      if (!controller.signal.aborted && !authorizationError(error)) { setTree(null); setTreeError(true); }
    }).finally(() => { if (!controller.signal.aborted) setTreeLoading(false); });
    return () => { controller.abort(); detailRequest.current?.abort(); };
  }, [active, revision]);
  useEffect(() => { if (selected && active) panelRef.current?.focus(); }, [selected, active]);
  async function loadDetail(departmentId: string, cursor?: string) {
    if (!active || treeLoading || !tree || !authorized.current) return;
    detailRequest.current?.abort(); const controller = new AbortController(); detailRequest.current = controller;
    setDetailLoading(true); setDetailError(false);
    if (!cursor) { setSelected(departmentId); setDetail(null); onOpen?.(); }
    try {
      const value = await fetchDepartment(departmentId, tree.generation_id, cursor, controller.signal);
      if (controller.signal.aborted || !authorized.current) return;
      setDetail(previous => cursor && previous ? { ...value, members: [...previous.members, ...value.members.filter(member => !previous.members.some(old => old.id === member.id))] } : value);
    } catch (error) {
      if (controller.signal.aborted || authorizationError(error)) return;
      if (error instanceof AiEngineeringApiError && error.status === 409) { clearDetail(); setTree(null); setRevision(value => value + 1); setNotice('组织已更新，请重新选择部门。'); }
      else setDetailError(true);
    } finally { if (!controller.signal.aborted) setDetailLoading(false); }
  }
  const byId = new Map(tree?.departments.map(d => [d.id, d]) ?? []);
  const children = new Map<string, Department[]>();
  for (const department of tree?.departments ?? []) if (department.parent_id) children.set(department.parent_id, [...(children.get(department.parent_id) ?? []), department]);
  const label = (department: Department) => department.id === tree?.root_id ? '公司组织' : department.name;
  function branch(department: Department) {
    const descendants = children.get(department.id) ?? []; const open = expanded.has(department.id);
    const firstLevel = department.parent_id === tree?.root_id;
    return <li key={department.id} style={firstLevel ? branchStyle(department.id) : undefined} className={`organization-branch${department.id === tree?.root_id ? ' organization-root' : ''}${firstLevel ? ' organization-group' : ''}`}><div className={`organization-node${selected === department.id ? ' is-selected' : ''}`}>
      {descendants.length > 0 && <button type="button" className="organization-toggle" disabled={!active || treeLoading} aria-label={`${open ? '收起' : '展开'}${label(department)}`} aria-expanded={open} onClick={() => setExpanded(previous => { const next = new Set(previous); if (next.has(department.id)) next.delete(department.id); else next.add(department.id); return next; })}>{open ? '⌄' : '›'}</button>}
      <button type="button" className="organization-name" disabled={!active || treeLoading} aria-pressed={selected === department.id} onClick={event => { triggerRef.current = event.currentTarget; void loadDetail(department.id); }}>{label(department)}</button>
    </div>{open && descendants.length > 0 && <ul className={department.id === tree?.root_id ? "organization-first-level" : undefined}>{descendants.map(branch)}</ul>}</li>;
  }
  const path: string[] = []; let current = selected ? byId.get(selected) : undefined;
  while (current) { path.unshift(label(current)); current = current.parent_id ? byId.get(current.parent_id) : undefined; }
  if (!active && !tree) return null;
  return <section className="organization-layout" aria-label="组织架构" aria-busy={treeLoading}><h1>组织架构</h1>
    {notice && <p role="status">{notice}</p>}
    {treeLoading && <p role="status">正在读取组织…</p>}
    {treeError && <p role="alert">组织目录暂不可用。<button type="button" onClick={() => setRevision(value => value + 1)}>重试组织</button></p>}
    {tree && <div className="organization-chart" aria-label="组织层级"><ul>{branch(byId.get(tree.root_id)!)}</ul></div>}
    {active && selected && tree && <aside ref={panelRef} tabIndex={-1} className="organization-detail" aria-label="部门详情" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); clearDetail(true); } }}><header><h3>{path[path.length - 1]}</h3><button type="button" className="organization-close" onClick={() => clearDetail(true)} aria-label="关闭部门详情"><span aria-hidden="true">×</span></button></header>
      {path.length > 1 && <p className="organization-path">{path.slice(0, -1).join(' / ')}</p>}
      {detail && <><dl className="organization-counts"><div><dt>总人数</dt><dd>{detail.total_count}</dd></div><div><dt>直属</dt><dd>{detail.direct_count}</dd></div></dl>
        <h4>成员</h4>
        {detail.members.length ? <ul className="organization-members">{detail.members.map(member => <li key={member.id}><div className="organization-member-person"><strong>{member.name}</strong>{member.status !== 'active' && <span className="organization-member-warning" title={member.status === 'disabled' ? '通讯录中标记为已禁用，请核实人员信息。' : '通讯录中标记为非活跃，请核实人员信息。'}>状态待核实</span>}</div><span className="organization-member-departments">{member.departments.length ? member.departments.map(d => d.id === tree.root_id ? '公司组织' : d.name).join('、') : '未归部门'}</span></li>)}</ul> : <p>暂无成员</p>}
      </>}
      {detailLoading && <p role="status">正在加载…</p>}
      {detailError && <p role="alert">详情加载失败。<button type="button" onClick={() => void loadDetail(selected, detail?.next_cursor ?? undefined)}>重试</button></p>}
      {detail?.next_cursor && !detailError && <button type="button" disabled={detailLoading} onClick={() => void loadDetail(selected, detail.next_cursor!)}>加载更多</button>}
      <footer className="organization-detail-footer">
        <div className="organization-updated"><span>更新于 <time dateTime={tree.completed_at} title={`${fullUpdatedTime.format(new Date(tree.completed_at))}（北京时间）`}>{updatedTime.format(new Date(tree.completed_at))}</time></span>{tree.freshness === 'hard_stale' && <span className="organization-stale" role="status">数据已过期</span>}</div>
        <details key={selected} className="organization-method"><summary>统计口径</summary><p>人数来自平台可读取的通讯录，不代表 HR 在职统计。总人数包含下级部门，同一人只计一次；直属仅统计本部门。</p>{selected === tree.root_id && <p>公司总人数包含未归部门人员。</p>}</details>
      </footer>
    </aside>}
  </section>;
}
