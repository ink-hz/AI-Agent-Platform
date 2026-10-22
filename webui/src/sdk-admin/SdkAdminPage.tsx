import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BookOpen, ChevronRight, Search, ShieldCheck } from 'lucide-react';
import { platformPath, type Account } from '../auth';
import { DocumentLink, portalClick, SdkDocumentReader } from './SdkDocumentReader';
import { isDocumentId, sdkAdminClient, SdkAdminApiError, type Catalog, type DocumentDetail, type DocumentKind, type DocumentList, type DocumentSummary, type SdkAdminClient } from './sdkAdminApi';
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
    const [state, setState] = useState<{
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
        const catalog = await client.catalog(controller.signal);
        if (active)
            setState({ catalog });
    }
    catch (error) {
        if (active)
            setState({ error });
    } })(); return () => { active = false; controller.abort(); }; }, [client, account.internal_user_id]);
    const meta = sdkAdminSection(section);
    return <div className="sdk-admin">
 <div className="sdk-main"><header className="sdk-page-header"><div><h1>{documentId ? '知识与成果阅读' : meta.title}</h1><p>{meta.subtitle}</p></div><div><a className="sdk-primary-link" href={platformPath('/sdk/')}>进入禅道 <ChevronRight size={15}/></a><p>需求与进度在禅道查看</p></div></header>
 {state.denied ? <Problem error={new SdkAdminApiError(403)}/> : state.error ? <Problem error={state.error}/> : !state.catalog ? <Loading /> : <AuthorizedBody key={`${section}:${account.internal_user_id}`} section={section} documentId={documentId} catalog={state.catalog} client={client}/>}
 </div></div>;
}
function AuthorizedBody({ section, documentId, catalog, client }: {
    section: SdkAdminSection;
    documentId?: string;
    catalog: Catalog;
    client: SdkAdminClient;
}) {
    if (documentId !== undefined)
        return <><KnowledgeStart /><Article key={documentId} id={documentId} client={client}/></>;
    return section === 'knowledge' ? <Knowledge catalog={catalog} client={client}/> : <Management section={section} catalog={catalog} client={client}/>;
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
    const [kind, setKind] = useState<DocumentKind>('knowledge');
    const [group, setGroup] = useState('');
    const [offset, setOffset] = useState(0);
    const [data, setData] = useState<DocumentList>();
    const [error, setError] = useState<unknown>();
    const input = useRef<HTMLInputElement>(null);
    const limit = 30;
    useEffect(() => { let active = true; const controller = new AbortController(); setData(undefined); setError(undefined); client.documents({ q: query, kind, group: group || undefined, offset, limit }, controller.signal).then(value => { if (active)
        setData(value); }, err => { if (active)
        setError(err); }); return () => { active = false; controller.abort(); }; }, [query, kind, group, offset, client]);
    return <><KnowledgeStart /><div className="sdk-tabs" aria-label="成果类型">{(Object.keys(kindNames) as DocumentKind[]).map(value => <button key={value} aria-pressed={kind === value} onClick={() => { setKind(value); setGroup(''); setOffset(0); }}>{kindNames[value]}</button>)}</div>
 <section className="sdk-panel"><form className="sdk-search" onSubmit={e => { e.preventDefault(); setQuery(input.current?.value.trim() ?? ''); setOffset(0); }}><label className="sdk-search-field"><Search size={18}/><span className="sdk-visually-hidden">搜索资料</span><input ref={input} type="search" name="q" maxLength={200} placeholder="搜索资料" defaultValue={query}/></label><select aria-label="知识分组" value={group} onChange={e => { setGroup(e.target.value); setOffset(0); }}><option value="">全部分组</option>{catalog.groups.filter(g => kind === 'knowledge' ? /^\d\d-/.test(g.id) || g.id === 'root' || g.id === '_meta' : g.id === kind).map(g => <option key={g.id} value={g.id}>{g.title} ({g.count})</option>)}</select><button type="submit" className="sdk-search-submit">搜索</button></form><div className="sdk-results-heading"><h3>{kindNames[kind]}</h3><span>{data ? `${data.total} 项${query ? ` · “${query}”` : ''}` : '读取目录中'}</span></div>{error ? <Problem error={error}/> : data ? <DocumentRows items={data.items} empty="没有匹配的内容，请调整关键词或分组。"/> : <Loading />}<div className="sdk-pagination"><span>{data && data.total ? `${offset + 1}–${Math.min(offset + data.items.length, data.total)} / ${data.total}` : '—'}</span><button disabled={!data || offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button><button disabled={!data || offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>下一页</button></div></section></>;
}
const productViews = [{ title: '产品 / 项目', description: '查看产品、项目与需求的关系说明。', ids: ['DOC-OBJECT-STATES', 'DOC-RELATION-RULES'] }, { title: '版本计划与范围', description: '查看版本范围与执行记录。', ids: ['DOC-PLATFORM-PROCESS', 'DOC-EXECUTION-LEDGER'] }, { title: '需求基线与来源核定', description: '查看需求来源、关联规则与阶段分析。', ids: ['DOC-RELATION-RULES', 'DOC-OBJECT-STATES', 'DOC-ANALYSIS-STATUS'] }];
const deliveryViews = [{ title: '开发与集成', description: '查看开发流程、变更关系与源码分析。', ids: ['DOC-PLATFORM-PROCESS', 'DOC-RELATION-RULES', 'DOC-ANALYSIS-GIT'] }, { title: '构建与制品', description: '查看构建条件、制品要求与发布记录。', ids: ['DOC-OBJECT-STATES', 'DOC-PLATFORM-PROCESS', 'DOC-SDK-R0-RELEASE'] }, { title: '验证与质量', description: '查看验证流程与静态资料接收记录。', ids: ['DOC-KB-ACCEPTANCE', 'DOC-PLATFORM-PROCESS'] }, { title: '准出与交付', description: '查看准出要求与交付记录。', ids: ['DOC-SDK-R0-RELEASE', 'DOC-OBJECT-STATES', 'DOC-EXECUTION-LEDGER'] }, { title: '公开发布与反馈', description: '查看公开发布关联与反馈处理资料。', ids: ['DOC-ANALYSIS-GIT', 'DOC-RELATION-RULES', 'DOC-PLATFORM-PROCESS'] }];
const diagnosticViews = [{ title: '技术诊断', description: '查阅静态代码知识与资料接收范围。', ids: ['DOC-KB-ACCEPTANCE'] }, { title: '过程诊断', description: '查看阶段现状与 Git 分析。', ids: ['DOC-ANALYSIS-STATUS', 'DOC-ANALYSIS-GIT'] }, { title: '改进行动', description: '查看改进相关的执行记录与设计草案。', ids: ['DOC-EXECUTION-LEDGER', 'DOC-PORTAL-DESIGN'] }];
function Management({ section, catalog, client }: {
    section: Exclude<SdkAdminSection, 'knowledge'>;
    catalog: Catalog;
    client: SdkAdminClient;
}) {
    const [items, setItems] = useState<DocumentSummary[]>();
    const [error, setError] = useState<unknown>();
    const [view, setView] = useState(0);
    useEffect(() => { const controller = new AbortController(); let active = true; (async () => { try {
        const lists = await Promise.all((['process', 'analysis', 'design', 'acceptance', 'execution'] as DocumentKind[]).map(kind => client.documents({ kind, offset: 0, limit: 100 }, controller.signal)));
        const documents = new Map(lists.flatMap(l => l.items).map(doc => [doc.id, doc]));
        if (section === 'overview') {
            const missing = [...new Set(catalog.featured_ids)].filter(id => !documents.has(id));
            // Fetch only the named recommendations, at most four details concurrently.
            for (let start = 0; start < missing.length; start += 4) {
                if (!active) return;
                const details = await Promise.all(missing.slice(start, start + 4).map(id => client.document(id, controller.signal)));
                details.forEach(doc => documents.set(doc.id, doc));
            }
        }
        if (active)
            setItems([...documents.values()]);
    }
    catch (err) {
        if (active)
            setError(err);
    } })(); return () => { active = false; controller.abort(); }; }, [client, catalog, section]);
    if (error)
        return <Problem error={error}/>;
    if (!items)
        return <Loading />;
    if (section === 'overview')
        return <Overview catalog={catalog} items={items}/>;
    const views = section === 'products' ? productViews : section === 'delivery' ? deliveryViews : diagnosticViews;
    const selected = views[view];
    return <><div className={`sdk-tabs${section === 'delivery' ? ' sdk-stage-tabs' : ''}`} aria-label="二级视图">{views.map((v, i) => <button key={v.title} aria-pressed={view === i} onClick={() => setView(i)}>{section === 'delivery' && <span>{String(i + 1).padStart(2, '0')}</span>}{v.title}</button>)}</div><section className="sdk-panel"><div className="sdk-panel-heading"><h2>{selected.title}</h2><p>{selected.description}</p></div><DocumentRows items={selected.ids.flatMap(id => items.filter(doc => doc.id === id))}/></section>{section === 'diagnostics' && <PortalLink className="sdk-back sdk-spaced" path="/sdk-admin/knowledge">查阅代码知识 →</PortalLink>}</>;
}
function Overview({ catalog, items }: {
    catalog: Catalog;
    items: DocumentSummary[];
}) {
    const featured = [...new Set(catalog.featured_ids)].flatMap(id => items.filter(item => item.id === id));
    return <><section className="sdk-panel"><div className="sdk-panel-heading"><h2>工作入口</h2></div><ol className="sdk-actions"><li><span>01</span><div><PortalLink path="/sdk-admin/products">产品与版本</PortalLink><p>查阅产品、版本与需求范围资料。</p></div></li><li><span>02</span><div><PortalLink path="/sdk-admin/delivery">研发与交付</PortalLink><p>按开发、构建、验证和交付阶段查阅资料。</p></div></li><li><span>03</span><div><PortalLink path="/sdk-admin/diagnostics">诊断与改进</PortalLink><p>查阅技术知识与阶段分析。</p></div></li></ol></section><section className="sdk-panel sdk-spaced"><div className="sdk-panel-heading"><h2>推荐阅读</h2></div><DocumentRows items={featured.length ? featured : items.slice(0, 5)}/><PortalLink className="sdk-panel-footer" path="/sdk-admin/knowledge">浏览全部知识与成果 →</PortalLink></section></>;
}
