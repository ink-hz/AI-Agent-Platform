import { platformPath } from './auth';
import { validWebEntry } from './platform/webEntryPolicy';
import type { Application, Channel, Counts, DashboardOverview, Metric, SourceEvidence } from './runtimeDashboardTypes';

const units: Record<Metric['unit'], string> = { session:'个会话', turn:'轮', delivery:'次送达', work:'项工作', result:'项成果', document:'篇文档', project:'个项目', task:'项任务', application:'个应用', agent:'个 Agent' };
const statuses = { observed:'已观测', partial:'部分覆盖', unavailable:'未知', not_applicable:'不适用' };
const reasons: Record<string,string> = {
  source_coverage_partial:'仅统计已知来源', source_coverage_or_input_time_partial:'来源覆盖或输入时间不完整', input_time_proxy:'输入时间含记录创建时间代理',
  source_not_configured:'未配置数据源', source_unavailable:'来源不可用', source_read_failed:'来源读取失败', delivery_not_proven:'无可验证的送达回执', delivery_evidence_unavailable:'送达证据不可用',
  workspace_not_conversational:'文档或业务工作台不统计对话', business_permission_denied:'当前账号无业务数据权限', task_metric_not_supplied:'业务来源未提供任务指标',
  complete_answer_not_proven:'无完整回答证据', hr_metadata_unavailable:'HR 元数据不可用', embedded_workspace:'内嵌业务工作台', health_not_configured:'未配置健康观测',
  health_http_error:'健康端点返回错误', health_unavailable:'健康观测不可用', health_not_ready:'服务尚未就绪',
  observation_missing:'缺少宿主观测', observation_invalid:'宿主观测格式无效', observation_future:'宿主观测时间超前', observation_stale:'宿主观测已过期',
  replica_limit_exceeded:'副本记录超过读取上限', replica_unavailable:'副本不可用', replica_payload_invalid:'副本记录校验失败', source_completeness_unknown:'来源完整性未知', source_count_mismatch:'来源记录数量不一致', source_failed:'来源同步失败', source_stale:'来源观测已过期',
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
export function SourceDetails({source}: {source:SourceEvidence}) {
  const proxy = source.input_time_basis === 'record_created_at' || source.input_time_basis === 'mixed';
  return <div className="runtime-source">
    <p>数据：{{fresh:'新鲜',stale:'已过期',unavailable:'未知',not_applicable:'不适用'}[source.status]} · {source.complete ? '来源读取完整' : '来源覆盖不完整'}</p>
    <p>来源 {source.source_id} · {source.source_kind}{source.channel ? ` / ${source.channel}` : ''}{source.channels.length ? ` · 渠道 ${source.channels.filter(Boolean).join('、')}` : ''}</p>
    <p>范围：{scopes[source.permission_scope] ?? source.permission_scope}{source.reader_role ? ` · 读取角色 ${source.reader_role}` : ''}</p>
    <p>源观测 {evidenceTime(source.observed_at)} · 发布/同步 {evidenceTime(source.published_at)} · 检查 {evidenceTime(source.checked_at)}</p>
    {source.input_time_basis && <p>输入归窗：{proxy ? '含记录创建时间代理' : source.input_time_basis === 'inputs_created_at' ? 'HR 原生 inputs.created_at' : '原始提问时间 question_at'}{source.missing_question_time_records != null ? ` · 缺提问时间 ${source.missing_question_time_records} 条` : ''}</p>}
    {source.error && <p className="runtime-warning">{evidenceReason(source.error)}</p>}
  </div>;
}
const runtimeMethods: Record<string,string> = {host_loopback:'宿主回环观测',safe_http:'固定健康端点观测'};
const deploymentEvidence: Record<string,string> = {ready_release_sha:'就绪端点实际版本',immutable_catalog_release:'不可变文档目录发布',configured_package_release:'业务包发布配置'};
const businessLabels:Record<string,string>={registered_vocs:'已入库 VOC',documents:'知识文档',published_documents:'已发布文档',visible_projects:'可见项目',tasks:'可见任务',works:'工作项',results:'保存成果',complete_answers:'完整回答'};
function ApplicationCard({app}: {app:Application}) {
  const runtime = app.runtime;
  const observed = runtime.coverage === 'observed' && !runtime.stale;
  const state = observed ? {healthy:'运行健康',degraded:'运行异常',offline:'运行离线',unknown:'运行未知'}[runtime.state] : '运行未知';
  const href = app.can_use && app.entry_path && (app.kind === 'agent'
    ? validWebEntry({kind:'independent_service',path:app.entry_path})
    : (app.id === 'sdk' && app.entry_path === '/sdk/') || (app.id === 'manufacturing' && app.entry_path === '/manufacturing/')) ? app.entry_path : null;
  return <article className="runtime-application" data-application-id={app.id} data-application-kind={app.kind}>
    <header><h3>{app.name}</h3><span>{app.kind === 'agent' ? '独立 Agent 服务' : '业务工作台'}</span></header>
    {href && <a href={platformPath(href)}>打开工作台 ↗</a>}
    <p className="runtime-health">{runtime.coverage === 'not_applicable' ? '运行观测不适用 · 内嵌工作台' : state}{runtime.stale ? ' · 健康证据已过期' : runtime.coverage === 'not_configured' ? ' · 未配置健康观测' : runtime.coverage === 'not_observed' ? ' · 尚未观测' : ''}</p>
    <p>运行观测 {evidenceTime(runtime.observed_at)} · 检查 {evidenceTime(runtime.checked_at)}{runtime.method ? ` · ${runtimeMethods[runtime.method] ?? runtime.method}` : ''}</p>
    {runtime.error && <p className="runtime-warning">{evidenceReason(runtime.error)}</p>}
    <SourceDetails source={app.source}/>
    {app.kind === 'agent' ? <><h4>所选窗口输入群组</h4><UsageCounts counts={app.usage.window}/><details><summary>来源保留记录</summary><UsageCounts counts={app.usage.retained}/></details><p>最近输入锚点 {evidenceTime(app.usage.last_activity_at)}</p></> : <p>文档与业务指标独立计量，不加入会话或用户轮次。</p>}
    {Object.entries(app.business_metrics).map(([key,metric])=><div className="runtime-business" key={key}><h4>{businessLabels[key] ?? metric.definition}</h4><MetricValue metric={metric}/><p>{metric.definition}</p>{app.business_sources[key] && <SourceDetails source={app.business_sources[key]}/>}</div>)}
    {app.dataset && <p className="runtime-dataset">数据集：{app.dataset.label ?? '未提供标签'} · {({demo:'演示',test:'测试',production:'正式'} as Record<string,string>)[app.dataset.mode] ?? app.dataset.mode ?? '类型未知'}</p>}
    <p className="runtime-deployment">实际版本：{app.deployment.version && app.deployment.evidence ? <><code>{app.deployment.version}</code> · {deploymentEvidence[app.deployment.evidence] ?? app.deployment.evidence} · 观测 {evidenceTime(app.deployment.observed_at)}</> : '未知（无发布证据）'}</p>
  </article>;
}
function ChannelCard({channel, historical=false}: {channel:Channel;historical?:boolean}) {
  return <article className="runtime-channel"><h3>{channel.name}</h3><p>{historical ? '历史来源' : channel.configured ? '已配置渠道 Agent' : '配置状态未知'} · 不计独立服务使用总数</p><SourceDetails source={channel.source}/><h4>所选窗口输入群组</h4><UsageCounts counts={channel.usage.window}/><details><summary>来源保留记录</summary><UsageCounts counts={channel.usage.retained}/></details></article>;
}
export function RuntimeDashboard({data}: {data:DashboardOverview}) {
  const summary=data.summary;
  const names=new Map(data.applications.filter(app=>app.kind==='agent').map(app=>[app.id,app.name]));
  return <>
    <section className="runtime-coverage" aria-label="数据覆盖"><h2>数据覆盖</h2>
      <p>正式应用 {data.inventory.applications} 个 · 独立 Agent 服务 {data.inventory.agent_services} 个 · 业务工作台 {data.inventory.business_workspaces} 个 · 已配置 Agent {data.inventory.configured_agents} 个</p>
      <p>健康观测覆盖 {summary.observed_runtime}/{summary.expected_runtime} · 健康 {summary.healthy} · 异常 {summary.abnormal} · 未知 {summary.unknown}</p>
      <p>独立服务数据覆盖 {summary.covered_sources}/{summary.expected_sources} · {{complete:'完整覆盖',partial:'部分覆盖',unavailable:'未知'}[summary.status]}</p>
      <p>快照 {evidenceTime(data.generated_at)} · 上海时区 · 输入时间窗口 [{evidenceTime(data.window.start)}, {evidenceTime(data.window.end)})</p>
      <p>近 15 分钟有输入的独立 Agent：<MetricValue metric={summary.active_agents_15m}/></p>
    </section>
    <section aria-label="当前独立服务使用"><h2>当前独立服务使用 · 近 {data.window.days} 天</h2><UsageCounts counts={summary.usage}/><p>仅统计当前独立 Agent；渠道与历史来源、工作台业务指标分别呈现。回复/送达表示同一输入群组当前记录状态，不代表窗口内回复/送达事件或任务完成。</p></section>
    <section aria-label="近24小时活动"><h2>近 24 小时活动</h2><p>输入窗口 [{evidenceTime(data.rolling_24h.start)}, {evidenceTime(data.rolling_24h.end)})</p><UsageCounts counts={data.last_24h}/></section>
    <div className="runtime-insights"><section><h2>近 {data.window.days} 天输入趋势</h2><div className="runtime-table-scroll"><table><caption>上海自然日 · 同一输入群组的当前回复与送达状态</caption><thead><tr><th scope="col">日期</th><th scope="col">用户输入</th><th scope="col">已有回复记录</th><th scope="col">已确认送达</th></tr></thead><tbody>{data.trend.map(day=><tr key={day.date}><th scope="row">{day.date}</th><td><MetricValue metric={day.turns}/></td><td><MetricValue metric={day.answered_turns}/></td><td><MetricValue metric={day.deliveries}/></td></tr>)}</tbody></table></div></section>
    <section><h2>当前独立服务输入排名</h2><ol className="runtime-ranking">{data.ranking.filter(row=>names.has(row.application_id) && row.turns.value !== null).map(row=><li key={row.application_id}><span>{names.get(row.application_id)}</span><MetricValue metric={row.turns}/></li>)}</ol><p>仅列有已知输入量的独立服务；缺数据不按 0 排名。</p></section></div>
    <section><h2>独立 Agent 服务</h2><div className="runtime-application-grid">{data.applications.filter(app=>app.kind==='agent').map(app=><ApplicationCard app={app} key={app.id}/>)}</div></section>
    <section><h2>业务工作台</h2><div className="runtime-application-grid">{data.applications.filter(app=>app.kind==='workspace').map(app=><ApplicationCard app={app} key={app.id}/>)}</div></section>
    <section><h2>MetaBot / 飞书 Agent</h2><p>已配置 {data.channel_agents.length} 个渠道 Agent，独立展示，不计当前独立服务总数；无实时观测不代表停用。</p><div className="runtime-application-grid">{data.channel_agents.map(channel=><ChannelCard channel={channel} key={channel.id}/>)}</div></section>
    <details className="runtime-history"><summary>历史来源（{data.legacy_streams.length}）· 不计当前总数</summary><div className="runtime-application-grid">{data.legacy_streams.map(channel=><ChannelCard channel={channel} historical key={channel.id}/>)}</div></details>
  </>;
}
