import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, ArrowUpRight, BookOpen, Search } from "lucide-react";
import { platformPath, type Account } from "../../auth";
import "./hrResearch.css";
import { navigateIntelligence } from "./hrIntelligenceNavigation";

type Article = { id: string; title: string; excerpt: string; question: number | null; companies: string[]; kind: string };
type Catalog = { edition: string; analyzed_at: string; observed_at: string; covered_job_identities: number; articles: Article[]; companies: { id: string; name: string }[]; questions: { id: number; name: string }[] };
type SourceTarget = {company_key: string; job_id: string | null; query?: string};
type Document = Article & { edition: string; markdown: string; links: Record<string, string>; source_reference?: {edition:string; source_bundle_id:string; links:Record<string,SourceTarget>} | null };
function sourceChanges(document: Document, target?: SourceTarget): Record<string,string|null> {
  return {layer:"sources", source_edition:document.source_reference?.edition ?? null, research_company:target?.company_key ?? null, source_job:target?.job_id ?? null, source_q:target?.query ?? null, source_location:null,source_channel:null,source_offset:null};
}
function sourceHref(document: Document, target?: SourceTarget) {
  const query = new URLSearchParams(window.location.search);
  query.set("research", document.id); query.set("edition", document.edition);
  for (const [key,value] of Object.entries(sourceChanges(document,target))) {if(value)query.set(key,value);else query.delete(key);}
  return platformPath(`/hr/panorama?${query}`);
}
const params = () => new URLSearchParams(window.location.search);
const title = (value: string) => value.replace(/^Q\d+\s*/, "");

export function ResearchMarkdown({ document, onOpen }: { document: Document; onOpen: (id: string) => void }) {
  const markdown = (text: string, key: number) => <ReactMarkdown key={key} remarkPlugins={[remarkGfm]} skipHtml components={{
    a: ({ href, children }) => {
      const id = href && document.links[href];
      if (id) return <a href={platformPath(`/hr/panorama?research=${encodeURIComponent(id)}&edition=${encodeURIComponent(document.edition)}`)} onClick={event => { if (!event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) { event.preventDefault(); onOpen(id); } }}>{children}</a>;
      const source = href && document.source_reference?.links[href];
      if (source) return <a href={sourceHref(document,source)} title={source.job_id ? "查看归档岗位原文" : "查看该公司原始资料范围"} onClick={event => {if(!event.ctrlKey && !event.metaKey && !event.shiftKey && event.button===0){event.preventDefault(); navigateIntelligence(sourceChanges(document,source));}}}>{children}</a>;
      if (href && /^https?:\/\//i.test(href)) return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
      if (href?.startsWith("#")) return <a href={href}>{children}</a>;
      return <span title="分析阶段本地材料，未随本次发布">{children}（本地材料）</span>;
    },
    table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
  }}>{text}</ReactMarkdown>;
  // Render only authored evidence disclosures; arbitrary HTML is never enabled.
  const nodes = [];
  const expression = /<details>\s*<summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/gi;
  let cursor = 0;
  for (const match of document.markdown.matchAll(expression)) {
    nodes.push(markdown(document.markdown.slice(cursor, match.index), cursor));
    nodes.push(<details key={`evidence-${match.index}`} className="hr-research-evidence"><summary>{match[1].replace(/<[^>]*>/g, "")}</summary>{markdown(match[2], -1)}</details>);
    cursor = match.index! + match[0].length;
  }
  nodes.push(markdown(document.markdown.slice(cursor), cursor));
  return <div className="hr-research-prose">{nodes}</div>;
}

export function HrResearchWorkspace({ account, active = true, companyOptions }: { account: Account; active?: boolean; companyOptions?: {id:string;name:string}[] }) {
  const [location, setLocation] = useState(() => window.location.search);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [document, setDocument] = useState<Document | null>(null);
  const [failure, setFailure] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const articleTop = useRef<HTMLDivElement>(null);
  const query = new URLSearchParams(location);
  const company = query.get("research_company") ?? "";
  const question = query.get("question") ?? "";
  const search = query.get("q") ?? "";
  const selected = query.get("research");
  const edition = query.get("edition");
  const [searchDraft, setSearchDraft] = useState(search);
  useEffect(() => { setSearchDraft(search); }, [search]);
  useEffect(() => {
    const sync = () => setLocation(window.location.search);
    window.addEventListener("popstate", sync);
    window.addEventListener("platform:navigate", sync);
    return () => { window.removeEventListener("popstate", sync); window.removeEventListener("platform:navigate", sync); };
  }, []);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true); setFailure(null); setDocument(null); setCatalog(null);
    async function read(path: string) {
      const response = await fetch(platformPath(path), { credentials: "same-origin", signal: controller.signal });
      if (!response.ok) throw response.status;
      return response.json();
    }
    void (async () => {
      try {
        const next: Catalog = await read("/api/hr/panorama/research");
        if (!next.edition || !Array.isArray(next.articles) || !Array.isArray(next.companies) || !Array.isArray(next.questions)) throw 503;
        let content: Document | null = null;
        if (selected) {
          content = await read(`/api/hr/panorama/research/${encodeURIComponent(selected)}?edition=${encodeURIComponent(edition ?? next.edition)}`);
          if (!content || typeof content.markdown !== "string" || content.edition !== (edition ?? next.edition) || content.id !== selected) throw 503;
        }
        if (!controller.signal.aborted) { setCatalog(next); setDocument(content); }
      } catch (error) {
        if (!controller.signal.aborted) { setCatalog(null); setDocument(null); setFailure(typeof error === "number" ? error : 503); }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [account.internal_user_id, account.csrf_token, selected, edition, retry, active]);
  useEffect(() => { if (!loading && active) window.dispatchEvent(new Event("hr:intelligence-ready")); }, [loading, active]);
  const navigate = (changes: Record<string, string | null>) => {
    const next = params();
    if ("research_company" in changes && changes.research_company !== next.get("research_company")) {
      for (const key of ["source_job", "source_q", "source_location", "source_channel", "source_offset"]) next.delete(key);
    }
    for (const [key, value] of Object.entries(changes)) { if (value) next.set(key, value); else next.delete(key); }
    history.pushState({}, "", `${platformPath("/hr/panorama")}?${next}`);
    setLocation(window.location.search);
    window.dispatchEvent(new Event("platform:navigate"));
    articleTop.current?.scrollIntoView?.({ block: "start" });
  };
  const open = (id: string) => navigate({ research: id, edition: document?.edition ?? catalog?.edition ?? null });
  const articles = catalog?.articles.filter(item => (!company || item.companies.includes(company)) && (!question || String(item.question) === question) && (!search || `${item.title} ${item.excerpt}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))) ?? [];
  const visibleCompanies = companyOptions ?? catalog?.companies ?? [];
  const companyName = visibleCompanies.find(item => item.id === company)?.name ?? (company || undefined);
  return <section className="hr-research" aria-label="HR 情报研究">
    <header className="hr-research-header">
      <div><span className="hr-research-eyebrow">HR INTELLIGENCE · RESEARCH</span><h1>读懂业务，找到对的人。</h1><p>从岗位原文理解工作、技术与交付责任，让人才判断有据可循。</p></div>
      <a className="hr-research-archive" href={platformPath("/hr/panorama?view=archive")}>历史发布归档 <ArrowUpRight size={15} /></a>
    </header>
    {failure ? <div className="hr-research-state" role="alert"><h2>{failure === 401 ? "登录状态已失效" : failure === 403 ? "当前账号没有 HR 情报权限" : failure === 404 ? "这份研究或指定版本暂不可用" : "情报暂时无法读取"}</h2><p>{failure === 404 ? "保留原引用，不会自动替换成另一份内容。" : "研究内容仅向获准的 HR 工作台账号开放。"}</p>{failure === 401 ? <a href={platformPath("/login?return_path=%2Fhr%2Fpanorama")}>重新登录</a> : <button onClick={() => setRetry(value => value + 1)}>重新读取</button>}{failure === 404 && <button onClick={() => navigate({ research: null, edition: null })}>返回研究目录</button>}</div> : loading ? <div className="hr-research-state" role="status">正在读取研究…</div> : catalog && <>
      <div className="hr-research-dateline"><span><BookOpen size={15} /> {catalog.articles.length} 篇研究 · {catalog.companies.length} 家公司</span><span>材料截至 {catalog.observed_at} · 分析于 {catalog.analyzed_at}</span><span>本批阅读覆盖 {catalog.covered_job_identities.toLocaleString()} 个岗位身份</span></div>
      <div className="hr-research-layout">
        <aside className="hr-research-sidebar" aria-label="按公司阅读"><p>研究范围</p><button aria-pressed={!company} onClick={() => navigate({ research_company: null, research: null, edition: null })}>全部公司 <span>{catalog.articles.length}</span></button>{visibleCompanies.map(item => <button key={item.id} aria-pressed={company === item.id} onClick={() => navigate({ research_company: item.id, research: null, edition: null })}>{item.name}<span>{catalog.articles.filter(article => article.companies.includes(item.id)).length}</span></button>)}<div className="hr-research-scope-note">研究基于已归档的公开招聘材料。岗位记录不代表招聘人数；结论的适用范围与未知项见各篇正文。</div></aside>
        <main className="hr-research-main" ref={articleTop}>
          {document ? <article className="hr-research-article" key={document.id}><button className="hr-research-back" onClick={() => navigate({ research: null, edition: null })}><ArrowLeft size={16} /> 返回{companyName ?? "研究目录"}</button><div className="hr-research-article-meta">{document.kind === "analysis" ? "研究全文 · 原文依据可展开" : "分析阶段的证据与复核记录 · 保留原文"}</div><ResearchMarkdown document={document} onOpen={open} />
            {document.source_reference ? <div className="hr-research-source-actions">{document.companies.length ? document.companies.map(key => <a className="hr-research-source-link" key={key} href={sourceHref(document,{company_key:key,job_id:null})} onClick={event=>{if(!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&event.button===0){event.preventDefault();navigateIntelligence(sourceChanges(document,{company_key:key,job_id:null}));}}}>查看{visibleCompanies.find(c=>c.id===key)?.name ?? key}的原始资料 <ArrowUpRight size={15}/></a>) : <a className="hr-research-source-link" href={sourceHref(document)}>查看报告所用原始资料 <ArrowUpRight size={15}/></a>}</div> : document.source_reference === null ? <p className="hr-source-note">本报告对应的原始资料版本暂不可用，报告原文仍保留。</p> : null}</article> : <>
            <div className="hr-research-heading"><div><span className="hr-research-eyebrow">{companyName ? "COMPANY RESEARCH" : "THE RESEARCH LIBRARY"}</span><h2>{companyName ?? "从一个具体问题开始"}</h2></div><span>{articles.length} 篇研究</span></div>
            <form className="hr-research-filters" onSubmit={event => { event.preventDefault(); navigate({ q: searchDraft || null }); }}><label><Search size={18} /><input value={searchDraft} onChange={event => setSearchDraft(event.target.value)} placeholder="搜索研究标题与摘要" aria-label="搜索研究标题与摘要" /></label><button type="submit">搜索</button><select aria-label="按研究问题筛选" value={question} onChange={event => navigate({ question: event.target.value || null })}><option value="">全部研究问题</option>{catalog.questions.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></form>
            <div className="hr-research-cards">{articles.map(item => <a className="hr-research-card" key={item.id} href={platformPath(`/hr/panorama?research=${item.id}&edition=${catalog.edition}`)} onClick={event => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) { event.preventDefault(); open(item.id); } }}><div className="hr-research-card-meta"><span>{item.companies.map(id => catalog.companies.find(c => c.id === id)?.name).join(" / ")}</span><span>{catalog.questions.find(q => q.id === item.question)?.name}</span></div><h3>{title(item.title)}</h3><p>{item.excerpt}</p><span className="hr-research-read">阅读全文与依据 <ArrowUpRight size={16} /></span></a>)}</div>
            {!articles.length && <div className="hr-research-state">{company && !catalog.articles.some(item => item.companies.includes(company)) ? "这家公司尚无本批 AI 分析报告，原始资料仍可查阅。" : "没有符合当前条件的研究。"}<button onClick={() => navigate({ q: null, question: null })}>清除筛选</button></div>}
          </>}
        </main>
      </div>
    </>}
  </section>;
}
