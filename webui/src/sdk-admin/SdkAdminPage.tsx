import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BookOpen, Boxes, ChevronRight, CircleHelp, Compass, GitBranch, Layers, Search, ShieldCheck } from 'lucide-react';
import { platformPath, type Account } from '../auth';
import { DocumentLink, portalClick, safeExternal, SdkDocumentReader, statusLabel } from './SdkDocumentReader';
import { isDocumentId, sdkAdminClient, SdkAdminApiError, type Catalog, type DocumentDetail, type DocumentKind, type DocumentList, type DocumentSummary, type SdkAdminClient } from './sdkAdminApi';
import './sdkAdmin.css';
export type SdkAdminSection = 'overview' | 'products' | 'delivery' | 'diagnostics' | 'knowledge';
export interface SdkAdminPageProps {
    section: SdkAdminSection;
    documentId?: string;
    account: Account;
    client?: SdkAdminClient;
}
const sections = [{ id: 'overview', title: '总览', subtitle: '目标与下一动作', icon: Compass }, { id: 'products', title: '产品与版本', subtitle: '范围、来源与业务对象', icon: Boxes }, { id: 'delivery', title: '研发与交付', subtitle: '阶段标准与交接依据', icon: GitBranch }, { id: 'diagnostics', title: '诊断与改进', subtitle: '技术与过程中的问题', icon: CircleHelp }, { id: 'knowledge', title: '知识与成果', subtitle: '深入阅读与证据检索', icon: BookOpen }] as const;
const kindNames: Record<DocumentKind, string> = { knowledge: '代码知识', process: '流程与规范', analysis: '分析成果', design: '设计方案', acceptance: '接收记录', execution: '执行记录' };
function PortalLink({ path, children, className, current }: {
    path: string;
    children: ReactNode;
    className?: string;
    current?: boolean;
}) { return <a aria-current={current ? "page" : undefined} className={className} href={platformPath(path)} onClick={e => portalClick(e, path)}>{children}</a>; }
function Problem({ error }: {
    error: unknown;
}) { const status = error instanceof SdkAdminApiError ? error.status : 0; return <div className="sdk-state sdk-error" role="alert"><ShieldCheck size={26}/><h2>{status === 401 ? '请先登录' : status === 403 ? '无 SDK 阅读权限' : status === 404 ? '未找到此文档' : status === 503 ? '内容发布包暂不可用' : '内容读取失败'}</h2><p>{status === 403 ? '当前企业身份未获得 SDK 内容阅读授权，请联系 SDK 权限负责人。' : status === 503 ? '内容尚未配置，或完整性校验未通过。请联系内容发布负责人检查发布包。' : '请检查访问状态或稍后重新进入；当前未载入阅读内容。'}</p></div>; }
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
    const meta = sections.find(s => s.id === section)!;
    return <div className="sdk-admin"><aside className="sdk-sidebar"><div className="sdk-brand"><span className="sdk-brand-mark"><Layers size={23}/></span><div><strong>SDK 开发管理</strong><span>研发协作工作台</span></div></div><nav aria-label="SDK 工作区">{sections.map(({ id, title, subtitle, icon: Icon }) => <PortalLink key={id} current={section === id} className={`sdk-nav-link${section === id ? ' is-current' : ''}`} path={id === 'overview' ? '/sdk-admin' : `/sdk-admin/${id}`}><Icon size={19}/><span><strong>{title}</strong><small>{subtitle}</small></span>{section === id && <ChevronRight size={15}/>}</PortalLink>)}</nav><div className="sdk-sidebar-note"><ShieldCheck size={17}/><span>内部只读成果<br />业务操作进入禅道</span></div></aside>
 <div className="sdk-main"><header className="sdk-page-header"><div><div className="sdk-breadcrumb">SDK 工作区 <ChevronRight size={12}/> {meta.title}{documentId && <> <ChevronRight size={12}/> 文档阅读</>}</div><h1>{documentId ? '知识与成果阅读' : meta.title}</h1><p>{meta.subtitle} · {state.catalog ? `资料快照 ${new Date(state.catalog.generated_at).toLocaleString('zh-CN', { hour12: false })}` : '等待授权内容'}</p></div><a className="sdk-primary-link" href={platformPath('/sdk/')}>进入禅道 <ChevronRight size={15}/></a></header>
 {state.denied ? <Problem error={new SdkAdminApiError(403)}/> : state.error ? <Problem error={state.error}/> : !state.catalog ? <Loading /> : <><div className="sdk-scope-line"><span>当前范围：已发布的 SDK 内部成果</span><span>基线 <code>{state.catalog.baseline_id}</code></span><span>发布 <code>{state.catalog.release_id}</code></span></div><AuthorizedBody key={`${section}:${account.internal_user_id}`} section={section} documentId={documentId} catalog={state.catalog} client={client}/></>}
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
function DocumentRows({ items, empty = '当前发布包未收录本视图的成果。' }: {
    items: DocumentSummary[];
    empty?: string;
}) { return items.length ? <ul className="sdk-document-list">{items.map(doc => <li key={doc.id}><div className="sdk-doc-icon"><BookOpen size={19}/></div><div className="sdk-doc-main"><DocumentLink id={doc.id}>{doc.title}</DocumentLink><div className="sdk-doc-meta"><code>{doc.id}</code><span>{kindNames[doc.kind]}</span><span>{doc.level ?? doc.group}</span></div><p>{statusLabel(doc.content_state)} · {statusLabel(doc.review_status)} · {statusLabel(doc.runtime_validation)}</p></div><ChevronRight size={17}/></li>)}</ul> : <div className="sdk-empty"><BookOpen size={23}/><p>{empty}</p></div>; }
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
    return <><KnowledgeStart /><div className="sdk-section-intro"><div><span className="sdk-eyebrow">EVIDENCE & KNOWLEDGE</span><h2>从问题进入，沿证据深入</h2><p>代码知识保留原有层级；流程、分析与成果记录按用途分别阅读。全文检索覆盖当前选择的内容类型与分组。</p></div><span className="sdk-count"><strong>{catalog.counts.knowledge_pages}</strong>正式知识页</span></div><div className="sdk-tabs" aria-label="成果类型">{(Object.keys(kindNames) as DocumentKind[]).map(value => <button key={value} aria-pressed={kind === value} onClick={() => { setKind(value); setGroup(''); setOffset(0); }}>{kindNames[value]}</button>)}</div>
 <section className="sdk-panel"><form className="sdk-search" onSubmit={e => { e.preventDefault(); setQuery(input.current?.value.trim() ?? ''); setOffset(0); }}><label className="sdk-search-field"><Search size={18}/><span className="sdk-visually-hidden">全文搜索</span><input ref={input} type="search" name="q" maxLength={200} placeholder="搜索 ID、标题或正文中的关键词" defaultValue={query}/></label><select aria-label="知识分组" value={group} onChange={e => { setGroup(e.target.value); setOffset(0); }}><option value="">全部分组</option>{catalog.groups.filter(g => kind !== 'knowledge' || /^\d\d-/.test(g.id) || g.id === 'root' || g.id === '_meta').map(g => <option key={g.id} value={g.id}>{g.title} ({g.count})</option>)}</select><button type="submit" className="sdk-search-submit">搜索</button></form><div className="sdk-results-heading"><h3>{kindNames[kind]}</h3><span>{data ? `${data.total} 项${query ? ` · “${query}”` : ''}` : '读取目录中'}</span></div>{error ? <Problem error={error}/> : data ? <DocumentRows items={data.items} empty="没有匹配的内容，请调整关键词或分组。"/> : <Loading />}<div className="sdk-pagination"><span>{data && data.total ? `${offset + 1}–${Math.min(offset + data.items.length, data.total)} / ${data.total}` : '—'}</span><button disabled={!data || offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button><button disabled={!data || offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>下一页</button></div></section></>;
}
const productViews = [{ title: '产品 / 项目', description: '产品与项目的负责人、目标和日常状态以禅道为准。先阅读对象身份与关系规则，再进入权威业务对象。', ids: ['DOC-OBJECT-STATES', 'DOC-RELATION-RULES'] }, { title: '版本计划与范围', description: '版本范围需要核定组件、设备与平台条件。现有流程和执行记录提供边界依据，门户尚未采集实时版本计划。', ids: ['DOC-PLATFORM-PROCESS', 'DOC-EXECUTION-LEDGER'] }, { title: '需求基线与来源核定', description: '保留原始来源、对象类型与关联依据。来源缺少关联时需要核定，不能直接判定未开发。', ids: ['DOC-RELATION-RULES', 'DOC-OBJECT-STATES', 'DOC-ANALYSIS-STATUS'] }];
const deliveryViews = [{ title: '开发与集成', description: '从需求及变更关系核定开始，沿真实源码与分析成果检查范围。合入和代码操作进入权威系统。', ids: ['DOC-PLATFORM-PROCESS', 'DOC-RELATION-RULES', 'DOC-ANALYSIS-GIT'] }, { title: '构建与制品', description: '准确组件、工具链、运行与制品摘要是交接条件。现有有限源码输入不能视为完整构建或可晋级候选。', ids: ['DOC-OBJECT-STATES', 'DOC-PLATFORM-PROCESS', 'DOC-SDK-R0-RELEASE'] }, { title: '验证与质量', description: '静态知识接收与设备运行验证分别阅读。测试需要绑定准确构建及设备、固件、平台和配置条件。', ids: ['DOC-KB-ACCEPTANCE', 'DOC-PLATFORM-PROCESS'] }, { title: '准出与交付', description: '以实际准出、发送与接收证据判断交付。有限平台上线记录不替代 SDK 产品交付验收。', ids: ['DOC-SDK-R0-RELEASE', 'DOC-OBJECT-STATES', 'DOC-EXECUTION-LEDGER'] }, { title: '公开发布与反馈', description: '内部交付与 GitHub 公开映射分别核查。发布附件来源及外部反馈需由授权角色在原系统登记与处置。', ids: ['DOC-ANALYSIS-GIT', 'DOC-RELATION-RULES', 'DOC-PLATFORM-PROCESS'] }];
const diagnosticViews = [{ title: '技术诊断', description: '代码知识可作为静态诊断依据。既有技术诊断的服务与产物尚待登记；目前未接通设备、日志采集或运行诊断。', ids: ['DOC-KB-ACCEPTANCE'] }, { title: '过程诊断', description: '按观察窗口、覆盖范围和证据阅读阶段性分析。提交量与变更规模不直接推导个人效率或人数缺口。', ids: ['DOC-ANALYSIS-STATUS', 'DOC-ANALYSIS-GIT'] }, { title: '改进行动', description: '从已确认问题形成责任、接收条件与复查证据；实际行动和当前状态进入禅道核定。现有记录可用于追溯建设过程。', ids: ['DOC-EXECUTION-LEDGER', 'DOC-PORTAL-DESIGN'] }];
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
    return <><div className="sdk-section-intro"><div><span className="sdk-eyebrow">{section === 'delivery' ? 'DELIVERY WORKFLOW' : section === 'products' ? 'SCOPE & OBJECTS' : 'DIAGNOSIS & IMPROVEMENT'}</span><h2>{section === 'delivery' ? '沿交付阶段找到标准与证据' : section === 'products' ? '先核定目标、来源与对象' : '用证据解释问题，保留结论边界'}</h2><p>{section === 'delivery' ? '各阶段展示已收录的真实材料，执行与业务状态以权威系统为准。' : section === 'products' ? '产品、版本和需求的实时数据尚未接入；业务操作使用禅道。' : '技术分析、运行诊断和过程研究分别接收。'}</p></div></div><div className={`sdk-tabs${section === 'delivery' ? ' sdk-stage-tabs' : ''}`} aria-label="二级视图">{views.map((v, i) => <button key={v.title} aria-pressed={view === i} onClick={() => setView(i)}>{section === 'delivery' && <span>{String(i + 1).padStart(2, '0')}</span>}{v.title}</button>)}</div><div className="sdk-content-columns"><section className="sdk-panel"><div className="sdk-panel-heading"><span className="sdk-eyebrow">{section === 'delivery' ? `阶段 ${view + 1} / ${views.length}` : '当前视图'}</span><h2>{selected.title}</h2><p>{selected.description}</p></div><DocumentRows items={selected.ids.flatMap(id => items.filter(doc => doc.id === id))}/></section><aside className="sdk-context"><h3>下一动作</h3><p>{section === 'products' ? '核对来源与对象规则，在禅道确认具体产品、项目和需求范围。' : section === 'delivery' ? '阅读本阶段的流程与记录，核对准确输入、接收条件和缺少的证据。' : '先区分事实、推断与未知，再补充运行条件或过程证据。'}</p><a href={platformPath('/sdk/')} className="sdk-text-link">前往禅道工作区 →</a><AiStatus catalog={catalog}/>{section === 'diagnostics' && <PortalLink path="/sdk-admin/knowledge">查阅静态代码知识 →</PortalLink>}</aside></div></>;
}
function AiStatus({ catalog }: {
    catalog: Catalog;
}) { return <div className="sdk-ai-note"><span className="sdk-eyebrow">AI 辅助 · 未接入</span><p>{catalog.ai.reason}</p><small>可用于来源核定、证据检索与诊断建议。接通前需完成 SDK 授权工具、输入范围、引用与成果接收验证。</small></div>; }
function Overview({ catalog, items }: {
    catalog: Catalog;
    items: DocumentSummary[];
}) {
    const featured = [...new Set(catalog.featured_ids)].flatMap(id => items.filter(item => item.id === id));
    return <><section className="sdk-focus"><div><span className="sdk-eyebrow">当前关注</span><h2>让交付判断有明确的范围与依据</h2><p>当前可阅读发布包中的知识、流程、分析和接收记录。产品目标、版本里程碑与个人待办尚未采集，需要先在禅道确认本次交付对象。</p><PortalLink path="/sdk-admin/products" className="sdk-focus-link">确认目标与范围 <ChevronRight size={16}/></PortalLink></div><div className="sdk-focus-facts"><span>交付目标 / 目标日期</span><strong>未接入实时业务数据</strong><span>当前阅读基线</span><code>{catalog.baseline_id}</code></div></section><div className="sdk-content-columns"><div><section className="sdk-panel"><div className="sdk-panel-heading"><span className="sdk-eyebrow">NEXT STEPS</span><h2>从这里继续工作</h2><p>以下为阅读与接入动作，实际待办由禅道维护。</p></div><ol className="sdk-actions"><li><span>01</span><div><PortalLink path="/sdk-admin/products">核定来源与业务范围</PortalLink><p>先读对象身份和关联规则，再确认具体版本与需求。</p></div></li><li><span>02</span><div><PortalLink path="/sdk-admin/delivery">检查阶段交接依据</PortalLink><p>沿开发、构建、验证和交付找到流程、证据与记录。</p></div></li><li><span>03</span><div><PortalLink path="/sdk-admin/diagnostics">复核已知问题与补证方向</PortalLink><p>阅读过程分析；技术运行诊断接入仍待登记。</p></div></li></ol></section><section className="sdk-panel sdk-spaced"><div className="sdk-panel-heading"><h2>当前发布成果</h2><p>保留每篇文档自己的接收、设计或阶段报告状态。</p></div><DocumentRows items={featured.length ? featured : items.slice(0, 5)}/><PortalLink className="sdk-panel-footer" path="/sdk-admin/knowledge">浏览全部知识与成果 →</PortalLink></section></div><aside className="sdk-context"><h3>数据源与接入</h3><p>资料快照 {new Date(catalog.generated_at).toLocaleDateString('zh-CN')} 的发布包状态。</p>{catalog.sources.length ? <ul className="sdk-sources">{catalog.sources.map(source => <li key={source.id}><strong>{source.label}</strong><span>{statusLabel(source.state)}</span><details><summary>原始状态</summary><code>{source.state}</code></details><p>{source.description}</p>{source.href && (source.href === '/sdk/' ? <a href={platformPath('/sdk/')}>打开禅道 →</a> : safeExternal(source.href) ? <a href={source.href} target="_blank" rel="noopener noreferrer">查看来源 →</a> : <code>{source.href}</code>)}</li>)}</ul> : <p>发布包尚未声明数据源接入状态。</p>}<AiStatus catalog={catalog}/></aside></div></>;
}
