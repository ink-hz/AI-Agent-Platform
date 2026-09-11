import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, ChevronRight, Database, FileText, Search } from 'lucide-react';
import { platformPath, type Account } from '../../auth';
import { intelligenceScroller, navigateIntelligence } from './hrIntelligenceNavigation';
import type { SourceCatalog, SourceCompanyPage, SourceJob } from './hrSourceTypes';
import './hrResearch.css';
import './hrSources.css';

const LABELS: Record<string, string> = {succeeded:'已取得资料',partial:'部分覆盖',failed:'采集失败',not_observed:'尚未采集',empty_confirmed:'已核验无记录',open:'采集时开放',closed:'采集时关闭',unknown:'状态未明',social:'社招',campus:'校招',intern:'实习'};
const label = (value: string | null) => value ? LABELS[value] ?? value : '未提供';
const jobCountLabel = (count: number | null, state: string) => count === null || (count === 0 && state !== 'empty_confirmed') ? '本次未取得岗位记录' : `${count.toLocaleString()} 条岗位记录`;
const date = (value: string | null) => value ? new Date(value).toLocaleDateString('zh-CN') : '时间未提供';
function SourceLink({url, children}: {url: string; children: React.ReactNode}) { return /^https?:\/\//i.test(url) ? <a href={url} target="_blank" rel="noopener noreferrer">{children} <ArrowUpRight size={13}/></a> : <span>来源地址未提供</span>; }

export function HrSourceWorkspace({account, search, active, onCatalog}: {account: Account; search: string; active: boolean; onCatalog: (value: SourceCatalog | null) => void}) {
  const query = new URLSearchParams(search);
  const companyKey = query.get('research_company') ?? '';
  const edition = query.get('source_edition');
  const jobId = query.get('source_job');
  const keyword = query.get('source_q') ?? '';
  const location = query.get('source_location') ?? '';
  const channel = query.get('source_channel') ?? '';
  const offset = query.get('source_offset') ?? '0';
  const [catalog, setCatalog] = useState<SourceCatalog | null>(null);
  const [company, setCompany] = useState<SourceCompanyPage | null>(null);
  const [job, setJob] = useState<SourceJob | null>(null);
  const [failure, setFailure] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [draft, setDraft] = useState(keyword);
  const [companySearch, setCompanySearch] = useState('');
  const main = useRef<HTMLDivElement>(null);
  const requestInFlight = useRef(false);
  const listScroll = useRef(0);
  const pendingScroll = useRef<'top' | 'list' | null>(null);
  useEffect(() => setDraft(keyword), [keyword]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    requestInFlight.current=true;
    setLoading(true); setFailure(null); setCompany(null); setJob(null);
    const read = async (path: string) => {
      const response = await fetch(platformPath(path), {credentials:'same-origin', signal:controller.signal});
      if (!response.ok) throw response.status;
      return response.json();
    };
    void (async () => {
      try {
        const next: SourceCatalog = await read('/api/hr/panorama/sources');
        if (!next.edition || !Array.isArray(next.companies)) throw 503;
        if (edition && edition !== next.edition) throw 404;
        const selectedEdition = edition ?? next.edition;
        let page: SourceCompanyPage | null = null;
        let detail: SourceJob | null = null;
        if (companyKey) {
          const params = new URLSearchParams({edition:selectedEdition, offset, limit:'25'});
          if (keyword) params.set('q', keyword);
          if (location) params.set('location', location);
          if (channel) params.set('channel', channel);
          page = await read(`/api/hr/panorama/sources/${encodeURIComponent(companyKey)}?${params}`);
          if (!page || page.edition !== selectedEdition || page.company.company_key !== companyKey || !Array.isArray(page.items)) throw 503;
          if (jobId) {
            detail = await read(`/api/hr/panorama/sources/${encodeURIComponent(companyKey)}/jobs/${encodeURIComponent(jobId)}?edition=${encodeURIComponent(selectedEdition)}`);
            if (!detail || detail.job_id !== jobId || detail.company_key !== companyKey || detail.edition !== selectedEdition) throw 503;
          }
        } else if (jobId) throw 404;
        if (!controller.signal.aborted) {setCatalog(next); onCatalog(next); setCompany(page); setJob(detail);}
      } catch (error) {
        if (!controller.signal.aborted) {setCatalog(null); onCatalog(null); setCompany(null); setJob(null); setFailure(typeof error === 'number' ? error : 503);}
      } finally { if (!controller.signal.aborted) {requestInFlight.current=false;setLoading(false);} }
    })();
    return () => controller.abort();
  }, [account.internal_user_id, account.csrf_token, active, companyKey, edition, jobId, keyword, location, channel, offset, retry, onCatalog]);
  useEffect(() => {
    if (requestInFlight.current || loading || !active) return;
    window.dispatchEvent(new Event('hr:intelligence-ready'));
    if (!pendingScroll.current) return;
    const target = pendingScroll.current;
    pendingScroll.current = null;
    const id = requestAnimationFrame(() => {
      if (target === 'list') intelligenceScroller(main.current).scrollTop = listScroll.current;
      else main.current?.scrollIntoView?.({block:'start'});
    });
    return () => cancelAnimationFrame(id);
  }, [loading, active]);
  const selectCompany = (key: string | null) => {
    pendingScroll.current = 'top';
    navigateIntelligence({layer:'sources',research_company:key,source_edition:catalog?.edition ?? null,source_job:null,source_offset:null,source_q:null,source_location:null,source_channel:null,research:null,edition:null});
  };
  const filter = (changes: Record<string,string | null>) => navigateIntelligence({...changes,source_offset:null,source_job:null,source_edition:catalog?.edition ?? null});
  const openJob = (id: string) => {listScroll.current = intelligenceScroller(main.current).scrollTop; pendingScroll.current='top'; navigateIntelligence({source_job:id,source_edition:catalog?.edition ?? null});};
  const closeJob = () => {pendingScroll.current='list';navigateIntelligence({source_job:null});};
  const report = () => navigateIntelligence({layer:'research',research:null,edition:null});
  const companies = catalog?.companies.filter(c => `${c.name} ${c.aliases.join(' ')}`.toLowerCase().includes(companySearch.toLowerCase())) ?? [];
  return <section className="hr-research hr-sources" aria-label="原始资料">
    <header className="hr-research-header"><div><span className="hr-research-eyebrow">HR INTELLIGENCE · SOURCE LIBRARY</span><h1>先看资料，再形成判断。</h1><p>查阅清洗后的公司资料与岗位正文，保留采集范围和原始出处。</p></div><a className="hr-research-archive" href={platformPath('/hr/panorama?view=archive')}>历史发布归档 <ArrowUpRight size={15}/></a></header>
    {failure ? <div className="hr-research-state" role="alert"><h2>{failure===401?'登录状态已失效':failure===403?'当前账号没有 HR 情报权限':failure===404?'这份资料或指定版本暂不可用':'资料暂时无法读取'}</h2><p>{failure===404?'保留原版本引用，不用其他资料替代。':'请重新读取后继续。'}</p>{failure===401?<a href={platformPath('/login?return_path='+encodeURIComponent('/hr/panorama'+window.location.search))}>重新登录</a>:<button onClick={()=>setRetry(v=>v+1)}>重新读取</button>}{failure===404&&<button onClick={()=>navigateIntelligence({source_job:null,source_edition:null,research_company:null})}>返回资料目录</button>}</div> : <>
      {catalog && <div className="hr-research-dateline"><span><Database size={15}/>{catalog.companies.length} 家公司 · {catalog.job_count.toLocaleString()} 条岗位记录</span><span>资料采集截至 {date(catalog.observed_at)}</span><span>岗位记录不等于招聘人数</span></div>}
      <div className="hr-research-layout">
        <aside className="hr-research-sidebar" aria-label="资料公司"><p>全部采集范围</p><button aria-pressed={!companyKey} onClick={()=>selectCompany(null)}>全部公司 <span>{catalog?.companies.length}</span></button>{catalog?.companies.map(c=><button key={c.company_key} data-source-company={c.company_key} aria-pressed={companyKey===c.company_key} onClick={()=>selectCompany(c.company_key)}>{c.name}<span>{c.job_count === 0 && c.coverage_state !== 'empty_confirmed' ? '—' : c.job_count ?? '—'}</span></button>)}<div className="hr-research-scope-note">正文仅整理格式，不补写缺失信息。渠道采集失败不代表没有招聘需求。</div></aside>
        <main className="hr-research-main" ref={main}>
          {loading ? <div className="hr-research-state" role="status">正在读取资料…</div> : job && company ? <article className="hr-research-article hr-source-job-detail">
            <button className="hr-research-back" onClick={closeJob}><ArrowLeft size={16}/>返回岗位列表</button>
            <div className="hr-source-job-heading"><p>{company.company.name} · {label(job.channel)}</p><h2>{job.title}</h2><div>{job.location || '地点未提供'} · {label(job.status)} · {date(job.observed_at)}</div></div>
            <dl className="hr-source-fields">{job.fields.map((field,index)=><div key={`${field.label}-${index}`}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>
            {job.content_note && <p className="hr-source-note">{job.content_note}</p>}
            <section className="hr-source-body"><h3>岗位职责 · JD</h3><div>{job.duty || '本次资料未取得岗位职责正文。'}</div></section>
            <section className="hr-source-body"><h3>任职要求 · JR</h3><div>{job.requirement || '资料未单独列出任职要求；如与职责合并，见上方原文。'}</div></section>
            <footer className="hr-source-provenance"><SourceLink url={job.source_url}>{job.source_kind==='job'?'查看职位来源':'查看归档来源入口'}</SourceLink><details><summary>采集与定位信息</summary><dl><dt>原岗位标识</dt><dd>{job.public_job_key}</dd><dt>原始证据摘要</dt><dd>{job.evidence_sha256}</dd><dt>资料版本</dt><dd>{job.edition}</dd></dl></details></footer>
          </article> : company ? <>
            <div className="hr-research-heading"><div><span className="hr-research-eyebrow">COMPANY SOURCES</span><h2>{company.company.name}</h2><p className="hr-source-aliases">{company.company.aliases.join(' / ')}</p></div><button className="hr-source-report-link" onClick={report}>查看 AI 分析 <ArrowUpRight size={15}/></button></div>
            <div className="hr-source-coverage"><span>招聘资料：{label(company.company.coverage_state)}</span><span>公司资料：{label(company.company.document_state)}</span>{company.limitations.map((note,i)=><p key={i}>{note}</p>)}<details><summary>来源渠道与采集情况</summary>{company.channels.map((item,i)=><div className="hr-source-channel" key={i}><strong>{label(item.channel)}</strong><span>{label(item.state)} · {date(item.observed_at)} · {item.job_count===null?'记录数未确认':`${item.job_count} 条记录`}</span><SourceLink url={item.source_url}>来源入口</SourceLink></div>)}</details></div>
            <section className="hr-source-documents"><h3><FileText size={17}/>公司公开资料</h3>{company.documents.length?company.documents.map((doc,index)=><details key={index}><summary>{doc.title || '官网资料'}<span>{date(doc.observed_at)}</span></summary><div className="hr-source-document-text">{doc.text}</div><SourceLink url={doc.source_url}>查看原始页面</SourceLink></details>):<p className="hr-source-note">本次未取得可展示的公司资料正文，采集情况见上方来源渠道。</p>}</section>
            <div className="hr-source-list-title"><h3>公开岗位</h3><span>{company.total} 条记录</span></div>
            <form className="hr-source-filters" onSubmit={event=>{event.preventDefault();filter({source_q:draft||null});}}><label><Search size={16}/><input aria-label="搜索岗位正文" placeholder="搜索岗位、职责或要求" value={draft} onChange={event=>setDraft(event.target.value)}/></label><button type="submit">搜索</button><select aria-label="筛选岗位地点" value={location} onChange={event=>filter({source_location:event.target.value||null})}><option value="">全部地点</option>{company.locations.map(v=><option key={v}>{v}</option>)}</select><select aria-label="筛选来源渠道" value={channel} onChange={event=>filter({source_channel:event.target.value||null})}><option value="">全部渠道</option>{company.channel_options.map(v=><option value={v} key={v}>{label(v)}</option>)}</select></form>
            <div className="hr-source-job-list">{company.items.map(item=><button key={item.job_id} data-source-job={item.job_id} onClick={()=>openJob(item.job_id)}><div><h4>{item.title}</h4><p>{item.location || '地点未提供'}<span>{label(item.channel)}</span><span>{label(item.status)}</span></p></div><ChevronRight size={18}/></button>)}</div>
            {!company.items.length && <p className="hr-research-state">当前条件下没有岗位记录。</p>}
            <nav className="hr-source-pagination" aria-label="岗位分页"><button disabled={company.offset===0} onClick={()=>navigateIntelligence({source_offset:String(Math.max(0,company.offset-company.limit))})}>上一页</button><span>{company.total?`${company.offset+1}–${Math.min(company.offset+company.limit,company.total)} / ${company.total}`:'0 条记录'}</span><button disabled={company.offset+company.limit>=company.total} onClick={()=>navigateIntelligence({source_offset:String(company.offset+company.limit)})}>下一页</button></nav>
          </> : catalog && <>
            <div className="hr-research-heading"><div><span className="hr-research-eyebrow">THE SOURCE LIBRARY</span><h2>选择一家公司，查阅原始资料</h2></div></div>
            <label className="hr-source-company-search"><Search size={17}/><input aria-label="搜索公司或别名" placeholder="搜索公司或别名" value={companySearch} onChange={event=>setCompanySearch(event.target.value)}/></label>
            <div className="hr-source-company-grid">{companies.map(c=><button key={c.company_key} onClick={()=>selectCompany(c.company_key)}><div><h3>{c.name}</h3><ChevronRight size={18}/></div><p>{c.aliases.join(' / ')}</p><strong>{jobCountLabel(c.job_count,c.coverage_state)}</strong><footer><span>招聘 · {label(c.coverage_state)}</span><span>官网 · {label(c.document_state)}</span></footer></button>)}</div>
            {!companies.length&&<p className="hr-research-state">没有匹配的公司。</p>}
          </>}
        </main>
      </div>
    </>}
  </section>;
}
