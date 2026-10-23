import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BookOpen, ChevronRight, Search, ShieldCheck } from 'lucide-react';
import { platformPath, type Account } from '../auth';
import { DocumentLink, portalClick, SdkDocumentReader } from './SdkDocumentReader';
import { isDocumentId, sdkAdminClient, SdkAdminApiError, type Catalog, type DocumentDetail, type DocumentList, type DocumentSummary, type SdkAdminClient } from './sdkAdminApi';
import { sdkAdminSection } from './sdkAdminNavigation';
import { documentListDescription, kindNames } from './sdkDocumentPresentation';
import './sdkAdmin.css';
import type { SdkAdminSection } from './sdkAdminPaths';
export type { SdkAdminSection } from './sdkAdminPaths';
export interface SdkAdminPageProps {
    section: SdkAdminSection;
    documentId?: string;
    account: Account;
    client?: SdkAdminClient;
}
function PortalLink({ path, children, className, current }: {
    path: string;
    children: ReactNode;
    className?: string;
    current?: boolean;
}) { return <a aria-current={current ? "page" : undefined} className={className} href={platformPath(path)} onClick={e => portalClick(e, path)}>{children}</a>; }
function Problem({ error }: {
    error: unknown;
}) { const status = error instanceof SdkAdminApiError ? error.status : 0; return <div className="sdk-state sdk-error" role="alert"><ShieldCheck size={26}/><h2>{status === 401 ? '请先登录' : status === 403 ? '无 SDK 阅读权限' : status === 404 ? '未找到此文档' : '暂时无法读取资料'}</h2><p>{status === 403 ? '当前企业身份未获得 SDK 内容阅读授权，请联系 SDK 权限负责人。' : status === 401 ? '登录后重新打开资料。' : status === 404 ? '请返回目录查找其他资料。' : '资料暂时不可用，请稍后重试。'}</p></div>; }
function Loading() { return <div className="sdk-state" role="status"><span className="sdk-loader"/><p>正在读取授权内容…</p></div>; }
export function SdkAdminPage({ section, documentId, account, client = sdkAdminClient }: SdkAdminPageProps) {
    const needsCatalog = section === 'knowledge' || documentId !== undefined;
    const [state, setState] = useState<{
        allowed?: boolean;
        catalog?: Catalog;
        error?: unknown;
        denied?: boolean;
    }>({});
    useEffect(() => { const controller = new AbortController(); let active = true; setState({}); (async () => { try {
        const access = await client.access(controller.signal);
        if (!active)
            return;
        if (!access.allowed) {
            setState({ denied: true });
            return;
        }
        const catalog = needsCatalog ? await client.catalog(controller.signal) : undefined;
        if (active)
            setState({ allowed: true, catalog });
    }
    catch (error) {
        if (active)
            setState({ error });
    } })(); return () => { active = false; controller.abort(); }; }, [client, account.internal_user_id, needsCatalog]);
    const meta = sdkAdminSection(section);
    return <div className="sdk-admin">
 <div className="sdk-main"><header className="sdk-page-header"><div><h1>{documentId ? '知识与成果阅读' : meta.title}</h1><p>{meta.subtitle}</p></div><div><a className="sdk-primary-link" href={platformPath('/sdk/')}>进入禅道 <ChevronRight size={15}/></a><p>需求与进度在禅道查看</p></div></header>
 {state.denied ? <Problem error={new SdkAdminApiError(403)}/> : state.error ? <Problem error={state.error}/> : !state.allowed || (needsCatalog && !state.catalog) ? <Loading /> : <AuthorizedBody key={`${section}:${account.internal_user_id}`} section={section} documentId={documentId} catalog={state.catalog} client={client}/>}
 </div></div>;
}
function AuthorizedBody({ section, documentId, catalog, client }: {
    section: SdkAdminSection;
    documentId?: string;
    catalog?: Catalog;
    client: SdkAdminClient;
}) {
    if (documentId !== undefined)
        return <><KnowledgeStart /><Article key={documentId} id={documentId} client={client}/></>;
    return section === 'knowledge' ? catalog ? <Knowledge catalog={catalog} client={client}/> : <Loading /> : <Workspace section={section}/>;
}
function KnowledgeStart() {
    return <nav className="sdk-reading-start" aria-label="知识阅读起点"><strong>按既有层级阅读</strong><DocumentLink id="KB-INDEX-ROOT">分层目录（L0–L5）</DocumentLink><span>→</span><DocumentLink id="KB-NAV-004">阅读协议</DocumentLink><span>→ 系统架构 → 运行对象 → 对应流程</span></nav>;
}
function Article({ id, client }: {
    id: string;
    client: SdkAdminClient;
}) {
    const [data, setData] = useState<DocumentDetail>();
    const [error, setError] = useState<unknown>();
    useEffect(() => { let active = true; const controller = new AbortController(); setData(undefined); setError(undefined); if (!isDocumentId(id)) {
        setError(new SdkAdminApiError(404));
        return () => controller.abort();
    } client.document(id, controller.signal).then(doc => { if (active)
        setData(doc); }, err => { if (active)
        setError(err); }); return () => { active = false; controller.abort(); }; }, [id, client]);
    useEffect(() => { if (!data || !window.location.hash)
        return; let fragment: string; try {
        fragment = decodeURIComponent(window.location.hash.slice(1));
    }
    catch {
        return;
    } document.getElementById(fragment)?.scrollIntoView?.(); }, [data]);
    return <><PortalLink className="sdk-back" path="/sdk-admin/knowledge">← 返回知识与成果</PortalLink>{error ? <Problem error={error}/> : data ? <SdkDocumentReader document={data}/> : <Loading />}</>;
}
function DocumentRows({ items, empty = '暂无相关资料。' }: {
    items: DocumentSummary[];
    empty?: string;
}) { return items.length ? <ul className="sdk-document-list">{items.map(doc => <li key={doc.id}><div className="sdk-doc-icon"><BookOpen size={19}/></div><div className="sdk-doc-main"><DocumentLink id={doc.id}>{doc.title}</DocumentLink><p>{documentListDescription(doc)}</p></div><ChevronRight size={17}/></li>)}</ul> : <div className="sdk-empty"><BookOpen size={23}/><p>{empty}</p></div>; }
function Knowledge({ catalog, client }: {
    catalog: Catalog;
    client: SdkAdminClient;
}) {
    const [query, setQuery] = useState('');
    const [kind, setKind] = useState<'knowledge' | 'analysis'>('knowledge');
    const [group, setGroup] = useState('');
    const [offset, setOffset] = useState(0);
    const [data, setData] = useState<DocumentList>();
    const [error, setError] = useState<unknown>();
    const input = useRef<HTMLInputElement>(null);
    const limit = 30;
    useEffect(() => { let active = true; const controller = new AbortController(); setData(undefined); setError(undefined); client.documents({ q: query, kind, group: group || undefined, offset, limit }, controller.signal).then(value => { if (active)
        setData(value); }, err => { if (active)
        setError(err); }); return () => { active = false; controller.abort(); }; }, [query, kind, group, offset, client]);
    return <><KnowledgeStart /><div className="sdk-tabs" aria-label="成果类型">{(['knowledge', 'analysis'] as const).map(value => <button key={value} aria-pressed={kind === value} onClick={() => { setKind(value); setGroup(''); setOffset(0); }}>{kindNames[value]}</button>)}</div>
 <section className="sdk-panel"><form className="sdk-search" onSubmit={e => { e.preventDefault(); setQuery(input.current?.value.trim() ?? ''); setOffset(0); }}><label className="sdk-search-field"><Search size={18}/><span className="sdk-visually-hidden">搜索资料</span><input ref={input} type="search" name="q" maxLength={200} placeholder="搜索资料" defaultValue={query}/></label><select aria-label="知识分组" value={group} onChange={e => { setGroup(e.target.value); setOffset(0); }}><option value="">全部分组</option>{catalog.groups.filter(g => kind === 'knowledge' ? /^\d\d-/.test(g.id) || g.id === 'root' || g.id === '_meta' : g.id === kind).map(g => <option key={g.id} value={g.id}>{g.title} ({g.count})</option>)}</select><button type="submit" className="sdk-search-submit">搜索</button></form><div className="sdk-results-heading"><h3>{kindNames[kind]}</h3><span>{data ? `${data.total} 项${query ? ` · “${query}”` : ''}` : '读取目录中'}</span></div>{error ? <Problem error={error}/> : data ? <DocumentRows items={data.items} empty="没有匹配的内容，请调整关键词或分组。"/> : <Loading />}<div className="sdk-pagination"><span>{data && data.total ? `${offset + 1}–${Math.min(offset + data.items.length, data.total)} / ${data.total}` : '—'}</span><button disabled={!data || offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button><button disabled={!data || offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>下一页</button></div></section></>;
}
const workspaces = {
    overview: {
        title: '常用工作',
        entries: [
            { title: '禅道工作区', path: '/sdk/', description: '处理需求、任务与问题。' },
            { title: 'SDK 技术知识', path: '/sdk-admin/knowledge', description: '查找 API、设备适配与排障方法。' },
            { title: '诊断与改进', path: '/sdk-admin/diagnostics', description: '定位技术问题，查看已有分析结论。' },
        ],
    },
    products: {
        title: '需求与范围',
        entries: [
            { title: '禅道产品与需求', path: '/sdk/', description: '整理需求范围、负责人和评审事项。' },
            { title: 'SDK 技术知识', path: '/sdk-admin/knowledge', description: '确认接口能力、设备支持与实现约束。' },
        ],
    },
    delivery: {
        title: '研发协作',
        entries: [
            { title: '禅道研发工作区', path: '/sdk/', description: '跟进需求与任务，处理研发协作事项。' },
            { title: 'SDK 技术知识', path: '/sdk-admin/knowledge', description: '核对接口契约、集成方式与排障方法。' },
        ],
    },
    diagnostics: {
        title: '问题处理与分析',
        entries: [
            { title: '禅道问题处理', path: '/sdk/', description: '登记问题、分派责任并跟进处理。' },
            { title: 'SDK 排障知识', path: '/sdk-admin/knowledge', description: '按故障症状、设备和平台定位技术知识。' },
            { title: 'SDK阶段性现状盘点（2026-10-22）', path: '/sdk-admin/knowledge/DOC-ANALYSIS-STATUS', description: '需求与内部实现、验证和交付的阶段性分析。' },
            { title: 'Git变更覆盖与需求节奏复核（2026-10-22）', path: '/sdk-admin/knowledge/DOC-ANALYSIS-GIT', description: '源码变更覆盖、需求活动轨迹与待核实问题。' },
        ],
    },
};
function Workspace({ section }: { section: Exclude<SdkAdminSection, 'knowledge'> }) {
    const workspace = workspaces[section];
    return <section className="sdk-panel"><div className="sdk-panel-heading"><h2>{workspace.title}</h2></div><ol className="sdk-actions">{workspace.entries.map((entry, index) => <li key={entry.path}><span>{String(index + 1).padStart(2, '0')}</span><div>{entry.path === '/sdk/' ? <a href={platformPath(entry.path)}>{entry.title}</a> : <PortalLink path={entry.path}>{entry.title}</PortalLink>}<p>{entry.description}</p></div></li>)}</ol></section>;
}
