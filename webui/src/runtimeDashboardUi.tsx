import { platformPath } from './auth';
import { validWebEntry } from './platform/webEntryPolicy';
import { useWebCatalog } from './platform/WebCatalog';
import type { AgentCapabilityCard } from './brainTypes';
import type { Application, Channel, Counts, DashboardOverview, Metric, SourceEvidence } from './runtimeDashboardTypes';

const units: Record<Metric['unit'], string> = { session:'个会话', turn:'轮', delivery:'次送达', work:'项工作', result:'项成果', document:'篇文档', project:'个项目', task:'项任务', application:'个应用', agent:'个 Agent' };
const statuses = { observed:'已观测', partial:'部分覆盖', unavailable:'未知', not_applicable:'不适用' };
const reasons: Record<string,string> = {
  source_coverage_partial:'仅统计已知来源', source_coverage_or_input_time_partial:'来源覆盖或输入时间不完整', input_time_proxy:'输入时间含记录创建时间代理',
  source_not_configured:'未配置数据源', source_unavailable:'来源不可用', source_read_failed:'来源读取失败', delivery_not_proven:'无可验证的送达回执', delivery_evidence_unavailable:'送达证据不可用',
  workspace_not_conversational:'文档或业务工作台不统计对话', business_permission_denied:'当前账号无业务数据权限', task_metric_not_supplied:'业务来源未提供任务指标',
  complete_answer_not_proven:'无完整回答证据', hr_metadata_unavailable:'HR 元数据不可用', embedded_workspace:'内嵌业务工作台', health_not_configured:'未配置健康观测',
  health_http_error:'健康端点返回错误', health_unavailable:'健康观测不可用', health_not_ready:'服务尚未就绪',
  observation_missing:'等待健康观测', observation_invalid:'健康观测暂不可用', observation_future:'健康观测时间需要核对', observation_stale:'等待下一次健康观测',
  replica_limit_exceeded:'记录超过读取上限', replica_unavailable:'来源暂不可用', replica_payload_invalid:'来源记录校验失败', source_completeness_unknown:'仅有已保留的来源记录', source_count_mismatch:'来源记录数量需要核对', source_failed:'来源暂未完成更新', source_stale:'暂无新的来源记录',
  sdk_catalog_unavailable:'SDK 文档目录不可用', manufacturing_unavailable:'制造工作流不可用', manufacturing_summary_unavailable:'制造汇总读取失败',
  voc_duplicate_records:'VOC 列表存在重复记录，覆盖不完整', voc_cursor_repeated:'VOC 分页游标重复，覆盖不完整', voc_record_limit:'VOC 读取达到记录上限', voc_page_limit:'VOC 读取达到分页上限', voc_business_unavailable:'VOC 业务来源不可用',
};
export function evidenceReason(reason: string | null | undefined) { return reason ? reasons[reason] ?? `观测未能提供完整证据（${reason}）` : ''; }
export function evidenceTime(time: string | null | undefined) {
  return time ? new Date(time).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '未知';
}
export function MetricValue({metric}: {metric:Metric}) {
  return <span className={`runtime-metric metric-${metric.status}`} title={metric.definition}>
    <strong>{metric.value === null ? statuses[metric.status] : `${metric.value.toLocaleString('zh-CN')} ${units[metric.unit]}`}</strong>
    <small>{statuses[metric.status]}{metric.reason ? ` · ${evidenceReason(metric.reason)}` : ''}</small>
  </span>;
}
const countLabels:Record<keyof Counts,string>={sessions:'来源会话',turns:'用户输入',answered_turns:'已有回复记录',deliveries:'已确认送达'};
export function UsageCounts({counts}: {counts:Counts}) {
  return <dl className="runtime-counts">{(Object.keys(countLabels) as (keyof Counts)[]).map(key=><div key={key}><dt>{countLabels[key]}</dt><dd><MetricValue metric={counts[key]}/></dd></div>)}</dl>;
}
const scopes:Record<string,string>={platform_management:'平台管理可见范围',business_scope:'业务授权范围',current_actor_voc_management:'当前账号 VOC 管理可见范围',current_actor:'当前账号可见范围',current_actor_projects:'当前账号授权项目'};
export function SourceDetails({source,historical=false}: {source:SourceEvidence;historical?:boolean}) {
  const proxy = source.input_time_basis === 'record_created_at' || source.input_time_basis === 'mixed';
  const inactiveObservation=historical && ['source_stale','source_completeness_unknown'].includes(source.error ?? '');
  return <details className="runtime-source" data-source-status={source.status}>
    <summary>{historical ? '历史记录与统计口径' : '数据明细与统计口径'}</summary>
    <div className="runtime-source-body"><p>{historical ? '历史记录，未配置实时运行监测。' : source.status==='stale' ? '当前显示最近一次读取的来源记录，更新待确认。' : source.complete ? '来源读取完整' : '来源覆盖不完整'}</p>
    <p>来源 {source.source_id} · {source.source_kind}{source.channel ? ` / ${source.channel}` : ''}{source.channels.length ? ` · 渠道 ${source.channels.filter(Boolean).join('、')}` : ''}</p>
    <p>范围：{scopes[source.permission_scope] ?? source.permission_scope}{source.reader_role ? ` · 读取角色 ${source.reader_role}` : ''}</p>
    <p>源观测 {evidenceTime(source.observed_at)} · 发布/同步 {evidenceTime(source.published_at)} · 检查 {evidenceTime(source.checked_at)}</p>
    {source.input_time_basis && <p>输入归窗：{proxy ? '含记录创建时间代理' : source.input_time_basis === 'inputs_created_at' ? 'HR 原生 inputs.created_at' : '原始提问时间 question_at'}{source.missing_question_time_records != null ? ` · 缺提问时间 ${source.missing_question_time_records} 条` : ''}</p>}
    {source.error && !inactiveObservation && <p className="runtime-warning">{evidenceReason(source.error)}</p>}
    </div>
  </details>;
}
const runtimeMethods: Record<string,string> = {host_loopback:'服务健康观测',safe_http:'服务健康检查'};
const deploymentEvidence: Record<string,string> = {ready_release_sha:'就绪端点版本',immutable_catalog_release:'文档目录发布',configured_package_release:'业务包发布'};
const businessLabels:Record<string,string>={registered_vocs:'已入库 VOC',documents:'知识文档',published_documents:'已发布文档',visible_projects:'可见项目',tasks:'可见任务',works:'工作项',results:'保存成果',complete_answers:'完整回答'};
const visuals:Record<string,{glyph:string;accent:string;domain:string}>={
  'hr-bot':{glyph:'HR',accent:'people',domain:'人力资源'},
  'ai-fae-agent':{glyph:'FAE',accent:'support',domain:'技术支持'},
  'ai-daq-fae-agent':{glyph:'DAQ',accent:'testing',domain:'数采支持'},
  'ai-admin-agent':{glyph:'ADM',accent:'administration',domain:'行政服务'},
  voc:{glyph:'VOC',accent:'intelligence',domain:'客户洞察'},
  'marketing-prospecting-bot':{glyph:'PRO',accent:'prospecting',domain:'Marketing'},
  'marketing-inbound-bot':{glyph:'IN',accent:'inbound',domain:'Marketing'},
  'marketing-voice-bot':{glyph:'VO',accent:'voice',domain:'Marketing'},
  'marketing-intelligence-bot':{glyph:'INT',accent:'intelligence',domain:'Marketing'},
  'marketing-gtm-bot':{glyph:'GTM',accent:'strategy',domain:'Marketing'},
};
function visual(id:string) {return visuals[id] ?? {glyph:'AI',accent:'collaboration',domain:'业务服务'};}
function MetricNumber({metric}: {metric:Metric}) {
  const label=`${metric.value===null ? '未知' : `${metric.value.toLocaleString('zh-CN')} ${units[metric.unit]}`} · ${statuses[metric.status]}${metric.reason ? ` · ${evidenceReason(metric.reason)}` : ''}`;
  return <span className="runtime-number" data-metric-status={metric.status} aria-label={label} title={`${metric.definition} · ${label}`}>{metric.value===null ? '—' : metric.value.toLocaleString('zh-CN')}{metric.status==='partial' && <sup aria-hidden="true">*</sup>}</span>;
}
function ApplicationCard({app,metadata}: {app:Application;metadata?:AgentCapabilityCard}) {
  const runtime=app.runtime;
  const observed=runtime.coverage==='observed' && !runtime.stale;
  const state=observed ? {healthy:'运行健康',degraded:'需要关注',offline:'离线',unknown:'运行未知'}[runtime.state] : '运行未知';
  const tone=!observed ? 'checking' : {healthy:'active',degraded:'degraded',offline:'offline',unknown:'checking'}[runtime.state];
  const icon=visual(app.id);
  const href=app.can_use && app.entry_path && (app.kind==='agent'
    ? validWebEntry({kind:'independent_service',path:app.entry_path})
    : (app.id==='sdk' && app.entry_path==='/sdk/') || (app.id==='manufacturing' && app.entry_path==='/manufacturing/')) ? app.entry_path : null;
  const business=Object.entries(app.business_metrics);
  const primaryKeys=new Set(['works','results','registered_vocs','documents','published_documents','visible_projects','tasks']);
  return <article className={`fleet-agent-card runtime-agent-card agent-${icon.accent}`} data-application-id={app.id} data-application-kind={app.kind}>
    <div className="fleet-agent-head"><span className="fleet-avatar" aria-hidden="true">{app.kind==='workspace' ? app.id==='sdk' ? 'SDK' : 'MFG' : icon.glyph}</span><div className="fleet-agent-identity"><p>{app.kind==='workspace' ? '业务工作台' : metadata?.domain_group ?? icon.domain}</p><h3>{app.name}</h3></div><span className={`fleet-state ${app.kind==='workspace' ? 'checking' : tone}`}><i aria-hidden="true"/>{app.kind==='workspace' ? '工作台' : state}</span></div>
    <p className="fleet-agent-description">{metadata?.mission ?? (app.kind==='workspace' ? '文档与业务指标独立计量，不加入会话或用户轮次。' : '独立业务 Agent · 查看运行状态、使用情况与工作记录。')}</p>
    {app.kind==='agent' && <><div className="fleet-usage"><div><span>来源保留输入</span><strong><MetricNumber metric={app.usage.retained.turns}/></strong></div><div className="weekly-usage"><span>所选窗口输入</span><b><MetricNumber metric={app.usage.window.turns}/></b></div></div><div className="runtime-card-meta"><span>最近使用</span><time dateTime={app.usage.last_activity_at ?? undefined}>{app.usage.last_activity_at ? evidenceTime(app.usage.last_activity_at) : '暂无可用时间'}</time></div></>}
    {business.length>0 && <div className="runtime-business-highlights">{business.filter(([key])=>primaryKeys.has(key)).map(([key,metric])=><div key={key}><span>{businessLabels[key]}</span><strong><MetricNumber metric={metric}/></strong></div>)}</div>}
      {app.dataset && <p className="runtime-dataset">数据集：{app.dataset.label ?? '未提供标签'} · {({demo:'演示',test:'测试',production:'正式'} as Record<string,string>)[app.dataset.mode] ?? app.dataset.mode ?? '类型未知'}</p>}
    <div className="runtime-card-actions">{href ? <a className="runtime-entry" href={platformPath(href)}>打开工作台 ↗</a> : <span className="runtime-entry-unavailable">{app.can_use ? '暂无使用入口' : '使用需授权'}</span>}<details className="runtime-record-details"><summary>查看明细</summary><div className="runtime-record-body">
      <SourceDetails source={app.source}/>
      <details className="runtime-observation"><summary>{runtime.coverage==='not_applicable' ? '运行观测不适用' : '运行观测详情'}</summary>{runtime.coverage==='not_configured' && <p>未配置健康观测</p>}{runtime.coverage==='not_observed' && <p>尚未观测</p>}{runtime.stale && <p>等待下一次健康观测，当前运行状态未知。</p>}<p>运行观测 {evidenceTime(runtime.observed_at)} · 检查 {evidenceTime(runtime.checked_at)}{runtime.method ? ` · ${runtimeMethods[runtime.method] ?? runtime.method}` : ''}</p>{runtime.error && <p>{evidenceReason(runtime.error)}</p>}</details>
      {app.kind==='agent' && <><h4>所选窗口输入群组</h4><UsageCounts counts={app.usage.window}/><h4>来源保留记录</h4><UsageCounts counts={app.usage.retained}/></>}
      {business.length>0 && <div className="runtime-business-grid">{business.map(([key,metric])=><div className="runtime-business" key={key}><h4>{businessLabels[key] ?? metric.definition}</h4><MetricValue metric={metric}/><p>{metric.definition}</p>{app.business_sources[key] && <SourceDetails source={app.business_sources[key]}/>}</div>)}</div>}

      <p className="runtime-deployment">实际版本：{app.deployment.version && app.deployment.evidence ? <><code>{app.deployment.version}</code> · {deploymentEvidence[app.deployment.evidence] ?? app.deployment.evidence} · 观测 {evidenceTime(app.deployment.observed_at)}</> : '暂无发布版本记录'}</p>
    </div></details></div>
  </article>;
}
function ChannelCard({channel,historical=false}: {channel:Channel;historical?:boolean}) {
  const icon=visual(channel.agent_id);
  return <article className={`runtime-channel agent-${icon.accent}`}><span className="ranking-avatar" aria-hidden="true">{icon.glyph}</span><div className="runtime-channel-identity"><h3>{channel.name}</h3><p>{historical ? '历史来源' : 'MetaBot + 飞书配置'}</p></div><span className="runtime-configured">{historical ? '历史记录' : channel.configured ? '已配置' : '配置待确认'}</span><details><summary>历史记录</summary><div className="runtime-channel-history"><SourceDetails source={channel.source} historical/><h4>所选窗口输入群组</h4><UsageCounts counts={channel.usage.window}/><h4>来源保留记录</h4><UsageCounts counts={channel.usage.retained}/></div></details></article>;
}
function InputTrend({data}: {data:DashboardOverview}) {
  const maximum=Math.max(1,...data.trend.map(day=>day.turns.value ?? 0));
  const width=Math.max(420,data.trend.length*36), step=width/Math.max(1,data.trend.length);
  return <div className="runtime-chart-scroll"><svg className={`runtime-trend-chart${data.trend.length>14 ? ' is-long' : ''}`} role="img" aria-label={`近 ${data.window.days} 天用户输入趋势`} viewBox={`0 0 ${width} 180`}>
    <title>用户输入趋势，详细数值与覆盖情况见下方数据表</title>
    <defs><pattern id="runtime-trend-partial" patternUnits="userSpaceOnUse" width="6" height="6"><rect width="6" height="6" className="runtime-chart-pattern-base"/><path d="M-1,1 L1,-1 M0,6 L6,0 M5,7 L7,5" className="runtime-chart-pattern-line"/></pattern></defs>
    <line x1="0" y1="146" x2={width} y2="146" className="runtime-chart-baseline"/>
    {data.trend.map((day,index)=>{
      const height=day.turns.value===null ? null : (day.turns.value/maximum)*112;
      return <g key={day.date} className={day.turns.status==='partial' ? 'runtime-chart-day is-partial' : 'runtime-chart-day'}>
        <title>{day.date}：{day.turns.value===null ? '未知' : `${day.turns.value} 轮`} · {statuses[day.turns.status]}{day.turns.reason ? ` · ${evidenceReason(day.turns.reason)}` : ''}</title>
        {height===null ? <text x={index*step+step/2} y="138" className="runtime-chart-missing">?</text> : height===0 ? <circle cx={index*step+step/2} cy="146" r="3" className="runtime-chart-zero"><title>0 轮</title></circle> : <rect x={index*step+(step-20)/2} y={146-height} width="20" height={height} rx="3"/>}
        {(data.trend.length<=14 || index%5===0 || index===data.trend.length-1) && <>{height!==null && <text x={index*step+step/2} y={138-height} className="runtime-chart-value">{day.turns.value}</text>}<text x={index*step+step/2} y="168" className="runtime-chart-date">{day.date.slice(5).replace('-','/')}</text></>}
      </g>;
    })}
  </svg></div>;
}
export function RuntimeDashboard({data}: {data:DashboardOverview}) {
  const catalog=useWebCatalog();
  const cards=new Map((catalog?.catalog ?? []).map(card=>[card.agent_id,card]));
  const summary=data.summary;
  const names=new Map(data.applications.filter(app=>app.kind==='agent').map(app=>[app.id,app.name]));
  const ranking=data.ranking.filter(row=>names.has(row.application_id) && row.turns.value!==null);
  const maximum=Math.max(1,...ranking.map(row=>row.turns.value ?? 0));
  return <div className="runtime-home">
    <section className="summary-section" aria-label="当前独立服务使用"><div className="section-heading"><h2>运行概况</h2><span>最近刷新 {evidenceTime(data.generated_at)} · 页面可见时每 10 秒更新</span></div>
      <div className="fleet-summary-grid">
        <article className="fleet-summary-card"><span>已配置 Agent</span><strong>{data.inventory.configured_agents}</strong><p>{data.inventory.agent_services} 个 Web 服务 · {data.inventory.channel_agents} 个渠道 Agent</p></article>
        <article className="fleet-summary-card summary-running"><span>正常运行</span><strong>{summary.healthy}</strong><p>{summary.abnormal ? `${summary.abnormal} 个需要关注` : `${summary.observed_runtime}/${summary.expected_runtime} 个已完成健康观测`}{summary.unknown>0 ? ` · ${summary.unknown} 个待观测` : ''}</p></article>
        <article className="fleet-summary-card summary-total"><span>近 {data.window.days} 天用户输入</span><strong><MetricNumber metric={summary.usage.turns}/></strong><p>当前独立 Agent 服务 · 按输入轮次计量</p></article>
        <article className="fleet-summary-card summary-weekly"><span>近 24 小时用户输入</span><strong><MetricNumber metric={data.last_24h.turns}/></strong><p>近 15 分钟有输入：<MetricNumber metric={summary.active_agents_15m}/> 个 Agent</p></article>
      </div>
    </section>
    <section className="insight-grid" aria-label="使用趋势与排行">
      <article className="insight-card trend-card"><div className="insight-heading"><h2>近 {data.window.days} 天输入趋势</h2><span><MetricNumber metric={summary.usage.turns}/> 轮输入</span></div><InputTrend data={data}/><p className="runtime-chart-legend">实色：已观测 · 斜纹：部分覆盖 · ○：零输入 · ?：未知</p><details className="runtime-trend-details"><summary>查看每日数据</summary><div className="runtime-table-scroll"><table><caption>上海自然日 · 同一输入群组的当前回复与送达状态</caption><thead><tr><th scope="col">日期</th><th scope="col">用户输入</th><th scope="col">已有回复记录</th><th scope="col">已确认送达</th></tr></thead><tbody>{data.trend.map(day=><tr key={day.date}><th scope="row">{day.date}</th><td><MetricValue metric={day.turns}/></td><td><MetricValue metric={day.answered_turns}/></td><td><MetricValue metric={day.deliveries}/></td></tr>)}</tbody></table></div></details></article>
      <article className="insight-card ranking-card"><div className="insight-heading"><h2>活跃 Agent</h2><span>按所选窗口输入量排序</span></div>{ranking.length ? <ol className="ranking-list">{ranking.slice(0,3).map((row,index)=>{const icon=visual(row.application_id);return <li key={row.application_id}><span className="ranking-index">{String(index+1).padStart(2,'0')}</span><span className={`ranking-avatar agent-${icon.accent}`} aria-hidden="true">{icon.glyph}</span><div className="ranking-main"><div><strong>{names.get(row.application_id)}</strong><span><MetricNumber metric={row.turns}/> 轮</span></div><svg className="runtime-ranking-bar" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden="true"><rect width={(row.turns.value!/maximum)*100} height="4" rx="2"/></svg></div></li>})}</ol> : <p className="ranking-empty">暂无可展示的输入记录</p>}{ranking.length>3 && <details className="runtime-more-ranking"><summary>全部排行</summary><ol>{ranking.map(row=><li key={row.application_id}><span>{names.get(row.application_id)}</span><MetricValue metric={row.turns}/></li>)}</ol></details>}<p className="runtime-quiet-note">仅统计独立服务；缺少记录不会按零排名。</p></article>
    </section>
    <section className="runtime-recent-strip" aria-label="近24小时活动"><h2>近 24 小时</h2><UsageCounts counts={data.last_24h}/></section>
    <section className="agents-section"><div className="section-heading"><h2>Agent 运行情况</h2><span>{data.inventory.agent_services} 个独立服务</span></div><div className="fleet-agent-grid">{data.applications.filter(app=>app.kind==='agent').map(app=><ApplicationCard key={app.id} app={app} metadata={cards.get(app.id)}/>)}</div></section>
    <section className="agents-section"><div className="section-heading"><h2>业务工作台</h2><span>{data.inventory.business_workspaces} 个业务入口</span></div><div className="runtime-workspace-grid">{data.applications.filter(app=>app.kind==='workspace').map(app=><ApplicationCard key={app.id} app={app}/>)}</div></section>
    <section className="runtime-channels-section"><div className="section-heading"><h2>MetaBot / 飞书 Agent</h2><span>已配置 {data.channel_agents.length} 个</span></div><p className="runtime-quiet-note">保留配置清单与历史记录，通过 MetaBot／飞书使用；不提供平台内使用入口。</p><div className="runtime-channel-list">{data.channel_agents.map(channel=><ChannelCard key={channel.id} channel={channel}/>)}</div></section>
    <details className="runtime-evidence"><summary>统计说明与数据覆盖</summary><p>正式应用 {data.inventory.applications} 个 · 独立 Agent 服务 {data.inventory.agent_services} 个 · 业务工作台 {data.inventory.business_workspaces} 个 · 已配置 Agent {data.inventory.configured_agents} 个</p><p>健康观测覆盖 {summary.observed_runtime}/{summary.expected_runtime} · 健康 {summary.healthy} · 异常 {summary.abnormal} · 未知 {summary.unknown}</p><p>独立服务数据覆盖 {summary.covered_sources}/{summary.expected_sources} · {{complete:'完整覆盖',partial:'部分覆盖',unavailable:'待更新'}[summary.status]}</p><p>上海时区 · 输入窗口 [{evidenceTime(data.window.start)}, {evidenceTime(data.window.end)})</p><p>仅统计当前独立 Agent；渠道与历史来源、工作台业务指标分别呈现。回复/送达表示同一输入群组当前记录状态，不代表窗口内回复/送达事件或任务完成。* 表示时间口径或来源覆盖有限，具体信息见对应明细。</p><h3>所选窗口统计</h3><UsageCounts counts={summary.usage}/></details>
    <details className="runtime-history"><summary>历史来源（{data.legacy_streams.length}）</summary><p className="runtime-quiet-note">历史来源不计当前独立服务总数。</p><div className="runtime-channel-list">{data.legacy_streams.map(channel=><ChannelCard key={channel.id} channel={channel} historical/>)}</div></details>
  </div>;
}
