import type { SourceOverview } from './hrSourceTypes';

const names: Record<string,string> = {research_development:'研发',sales_marketing:'销售与市场',supply_chain:'供应链',manufacturing:'制造',operations:'运营',product:'产品',quality:'质量',corporate:'职能',other:'其他',graduate:'校招 / 实习',junior:'初级',mid:'中级',senior:'高级',unspecified:'未标注',campus:'校招',social:'社招',intern:'实习',unknown:'未识别'};
export const metricName = (name: string) => names[name] ?? name;
export const topMetrics = (values: Record<string,number> = {}, limit = 3) => Object.entries(values).filter(([,n])=>n>0).sort((a,b)=>b[1]-a[1] || a[0].localeCompare(b[0])).slice(0,limit);

function Distribution({title, note, values, total, limit = 8}: {title:string; note:string; values:Record<string,number>; total:number; limit?:number}) {
  const entries=topMetrics(values,100);
  const rows=(items:typeof entries)=>items.map(([name,count])=><div className="hr-source-distribution-row" key={name}><div><span>{metricName(name)}</span><strong>{count.toLocaleString()} <small>条</small></strong></div><div className="hr-source-distribution-track" aria-hidden="true"><i style={{width:`${Math.min(100,count/Math.max(1,total)*100)}%`}}/></div></div>);
  return <section className="hr-source-distribution"><h3>{title}</h3><p>{note}</p>{entries.length?<>{rows(entries.slice(0,limit))}{entries.length>limit&&<details><summary>展开其余 {entries.length-limit} 项</summary>{rows(entries.slice(limit))}</details>}</>:<p>本次没有可归类的记录。</p>}</section>;
}

export function HrSourceOverview({overview}:{overview:SourceOverview|null|undefined}) {
  if(!overview)return <p className="hr-source-note">这家公司尚无可用的规则聚合，仍可查阅已取得的资料和采集情况。</p>;
  const m=overview.metrics;
  return <section className="hr-source-overview" aria-label="招聘资料聚合">
    <div className="hr-source-overview-title"><div><span className="hr-research-eyebrow">RECRUITMENT LANDSCAPE</span><h2>招聘资料聚合</h2></div><span>全公司 {m.job_count.toLocaleString()} 条岗位记录</span></div>
    <p className="hr-source-overview-intro">按采集资料整理招聘结构与技术关键词。下方是规则统计，详细依据可在岗位原文中核对。</p>
    <div className="hr-source-distribution-grid">
      <Distribution title="职能构成" note="按岗位文本归类，每条记录归入一个主要职能。" values={m.job_families} total={m.job_count}/>
      <Distribution title="技术与业务方向" note="文本规则匹配；一条岗位可涉及多个方向。" values={m.directions} total={m.job_count}/>
      <Distribution title="技能关键词" note="提及该技能的岗位数，不代表全部为必备条件。" values={m.skills} total={m.job_count}/>
      <Distribution title="招聘地域" note="归档地点整理；多地岗位分别计入各地。" values={m.locations} total={m.job_count}/>
      <Distribution title="招聘类型" note="沿用归档规则识别的社招、校招与实习分类。" values={m.tracks} total={m.job_count}/>
      <Distribution title="经验层次" note="校招与实习合并一类；其余按文本归类，不是企业内部职级。" values={m.seniority} total={m.job_count}/>
    </div>
    <details className="hr-source-specialties"><summary>查看细分技术方向与统计口径</summary><Distribution title="细分技术方向" note="同一条记录可命中多个子方向，数量不可相加当作岗位总数。" values={m.secondary_directions} total={m.job_count} limit={12}/><p>复用同批归档的规则统计 v{overview.rules_schema_version}。技能和地域沿用归档前 20 项；未列出不代表不存在。单次采集不能判断新增或减少，岗位数不等于招聘人数、预算或实际投入。</p><p>资料版本：{overview.source_edition}<br/>聚合版本：{overview.edition}</p></details>
  </section>;
}
