import { useEffect, useState } from "react";

import { copyVisibleText } from "../../clipboard";
import type { HrPanoramaReport as PanoramaReport, HrPanoramaSnapshot } from "../../hrPanoramaTypes";

const NUMBER = new Intl.NumberFormat("zh-CN");
const DATE_TIME = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});

function time(value: string): string { return DATE_TIME.format(new Date(value)); }
function clusterValue(value: unknown): string {
  if (typeof value === "number") return NUMBER.format(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => clusterValue(item)).join("、");
  if (value && typeof value === "object") return Object.entries(value).map(([key, item]) => `${key} ${clusterValue(item)}`).join(" · ");
  return "待补充";
}

function countBy(items: HrPanoramaSnapshot[], selected: (item: HrPanoramaSnapshot) => string): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const item of items) counts.set(selected(item), (counts.get(selected(item)) ?? 0) + 1);
  return [...counts.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "zh-CN"));
}

type RecruitmentChanges = { added: number; removed: number; continued: number; unobserved: number; failedSourceIds: string[] };

export type HrPanoramaComparison =
  | { state: "loading" }
  | { state: "none"; currentSourceFailures: Record<string, string> }
  | { state: "unavailable" }
  | {
    state: "available";
    previousReport: PanoramaReport;
    currentSourceFailures: Record<string, string>;
    previousSourceFailures: Record<string, string>;
  };

function jobKey(item: HrPanoramaSnapshot): string { return `${item.sourceId}:${item.publicJobKey}`; }

function recruitmentChanges(report: PanoramaReport, comparison: HrPanoramaComparison): RecruitmentChanges | null {
  if (comparison.state !== "available") return null;
  const current = new Map(report.snapshots.map((item) => [jobKey(item), item]));
  const previous = new Map(comparison.previousReport.snapshots.map((item) => [jobKey(item), item]));
  const currentFailures = new Set(Object.keys(comparison.currentSourceFailures));
  const previousFailures = new Set(Object.keys(comparison.previousSourceFailures));
  let added = 0; let removed = 0; let continued = 0; let unobserved = 0;
  for (const [key, snapshot] of current) {
    if (snapshot.status !== "open" || currentFailures.has(snapshot.sourceId)) continue;
    const prior = previous.get(key);
    if (prior?.status === "open") continued += 1;
    else if (prior?.status === "closed" || (!prior && !previousFailures.has(snapshot.sourceId))) added += 1;
  }
  for (const [key, snapshot] of previous) {
    if (snapshot.status !== "open" || previousFailures.has(snapshot.sourceId)) continue;
    const latest = current.get(key);
    if (latest?.status === "closed" && !currentFailures.has(snapshot.sourceId)) removed += 1;
    else if (!latest && !currentFailures.has(snapshot.sourceId)) unobserved += 1;
  }
  return { added, removed, continued, unobserved, failedSourceIds: [...currentFailures] };
}

function comparisonMessage(comparison: HrPanoramaComparison): string {
  if (comparison.state === "loading") return "正在核对变化基线，当前报告仍可查看。";
  if (comparison.state === "unavailable") return "变化基线暂时不可用，当前报告仍可查看。";
  return "首次分析，暂无变化基线";
}

function currentFailureIds(comparison: HrPanoramaComparison): string[] {
  return comparison.state === "available" || comparison.state === "none" ? Object.keys(comparison.currentSourceFailures) : [];
}

type ReportView = "overview" | "social" | "campus" | "strategy" | "jobs" | "evidence";
type RecruitmentTrack = "social" | "campus" | "unknown";

const REPORT_VIEWS: Array<{ id: ReportView; label: string }> = [
  { id: "overview", label: "总览" },
  { id: "social", label: "社招" },
  { id: "campus", label: "校招" },
  { id: "strategy", label: "产品与业务方向" },
  { id: "jobs", label: "岗位明细" },
  { id: "evidence", label: "来源证据" },
];

function recruitmentTrack(item: HrPanoramaSnapshot): RecruitmentTrack {
  const searchable = `${item.title} ${item.dutyExcerpt} ${item.requirementExcerpt} ${item.sourceUrl}`.toLocaleLowerCase("zh-CN");
  if (/(校招|校园招聘|应届|毕业生|实习|campus|graduate|intern)/i.test(searchable)) return "campus";
  if (/(社招|社会招聘|社会人才|experienced|professional-hire)/i.test(searchable)) return "social";
  return "unknown";
}

function technicalDirection(item: HrPanoramaSnapshot): string {
  const searchable = `${item.title} ${item.dutyExcerpt} ${item.requirementExcerpt}`.toLocaleLowerCase("zh-CN");
  if (/(算法|人工智能|机器学习|视觉|点云|ai\b|slam)/i.test(searchable)) return "算法";
  if (/(光学|镜头|zemax|成像)/i.test(searchable)) return "光学";
  if (/(硬件|电子|电路|pcb|嵌入式)/i.test(searchable)) return "硬件";
  if (/(结构|机械|机电|模具|cad)/i.test(searchable)) return "结构";
  if (/(软件|前端|后端|客户端|java|c\+\+|python)/i.test(searchable)) return "软件";
  if (/(制造|工艺|质量|dqe|生产|供应链)/i.test(searchable)) return "制造工艺";
  return "其他";
}

function JobCards({ items, sourceById }: { items: HrPanoramaSnapshot[]; sourceById: Map<string, PanoramaReport["sources"][number]> }) {
  if (!items.length) return <p className="hr-panorama-empty-copy">本版没有可展示的匹配岗位记录。</p>;
  return <div className="hr-panorama-job-cards">{items.map((item) => <article key={item.snapshotId}>
    <span>{sourceById.get(item.sourceId)?.canonicalName ?? "关注公司"}</span><h3>{item.title}</h3><p>{item.location} · {item.dutyExcerpt}</p>
    <footer><a href={item.sourceUrl} rel="noreferrer" target="_blank">打开岗位来源 ↗</a><time dateTime={item.observedAt}>{time(item.observedAt)}</time></footer>
  </article>)}</div>;
}

function TrackView({ track, report, comparison, sourceById }: { track: Exclude<RecruitmentTrack, "unknown">; report: PanoramaReport; comparison: HrPanoramaComparison; sourceById: Map<string, PanoramaReport["sources"][number]> }) {
  const label = track === "social" ? "社招" : "校招";
  const items = report.snapshots.filter((item) => recruitmentTrack(item) === track);
  const unknown = report.snapshots.filter((item) => recruitmentTrack(item) === "unknown");
  const failures = new Set(currentFailureIds(comparison));
  return <section className="hr-panorama-track-view" data-report-view={track}>
    <header><div><p>{track === "social" ? "SOCIAL RECRUITING" : "CAMPUS RECRUITING"}</p><h2>{label}招聘</h2></div><strong>{items.length} 个可判定岗位</strong></header>
    <div className="hr-panorama-track-coverage">{report.sources.map((source) => {
      const count = items.filter((item) => item.sourceId === source.sourceId).length;
      const hasUnknown = unknown.some((item) => item.sourceId === source.sourceId);
      return <article key={source.sourceId}><strong>{source.canonicalName}</strong><span>{failures.has(source.sourceId)
        ? "本轮采集失败，无法判断"
        : count > 0 ? `${count} 个公开岗位`
          : hasUnknown ? `未识别到${label}标记，待确认` : `本轮没有可判定的${label}记录，待确认`}</span></article>;
    })}</div>
    {unknown.length > 0 && <p className="hr-panorama-track-warning">{unknown.length} 个岗位尚未识别招聘类型，未强行归入社招或校招。</p>}
    <JobCards items={items} sourceById={sourceById} />
  </section>;
}

export function formatHrPanoramaReportMarkdown(report: PanoramaReport, comparison: HrPanoramaComparison = { state: "none", currentSourceFailures: {} }): string {
  const changes = recruitmentChanges(report, comparison);
  const facts = new Map(report.insight.facts.map((fact) => [fact.factId, fact]));
  const sourceNames = new Map(report.sources.map((source) => [source.sourceId, source.canonicalName]));
  const observationTimes = report.snapshots.map((item) => item.observedAt).sort();
  const asOf = observationTimes[observationTimes.length - 1] ?? report.insight.createdAt;
  const geography = countBy(report.snapshots, (item) => item.location);
  const jobStatus = (status: HrPanoramaSnapshot["status"]) => status === "open" ? "公开招聘中" : status === "closed" ? "已下线" : "状态待确认";
  const lines = [
    `# 全景分析 · 第 ${report.insight.versionNumber} 版`, "",
    report.insight.summary, "",
    `- 覆盖公司：${report.sources.map((source) => source.canonicalName).join("、")}`,
    `- 分析时间：${report.insight.createdAt}`,
    `- 观测截至：${asOf}`,
    "", "## 研发方向", "",
    ...Object.entries(report.insight.directionClusters).map(([label, value]) => `- ${label}：${clusterValue(value)}`),
    "", "## 招聘变化", "",
    ...(changes ? [
      `- 新增岗位：${changes.added}`, `- 明确关闭：${changes.removed}`, `- 持续招聘：${changes.continued}`,
      `- 本次未再次采集到（待验证，不代表停止招聘）：${changes.unobserved}`,
      ...(changes.failedSourceIds.length ? [`- ${changes.failedSourceIds.map((id) => sourceNames.get(id) ?? "关注公司").join("、")}本轮采集失败，无法判断变化。`] : []),
    ] : [comparisonMessage(comparison), ...currentFailureIds(comparison).map((id) => `- ${sourceNames.get(id) ?? "关注公司"}本轮采集失败，无法判断变化。`)]),
    "", "## 地域分布", "",
    ...(geography.length ? geography.map(([location, count]) => `- ${location}：${count} 个岗位`) : ["暂无可用岗位地点。"]),
    "", "## 关键能力", "",
    ...(report.snapshots.length ? report.snapshots.map((item) => `- ${sourceNames.get(item.sourceId) ?? "关注公司"}｜${item.title}：${item.requirementExcerpt}`) : ["暂无可核验的岗位能力要求。"]),
    "", "## 公开事实", "",
    ...report.insight.facts.map((fact) => `- ${fact.text}\n  - 来源：${fact.sourceUrl}\n  - 观测于 ${fact.observedAt}`),
    "", "## AI 推断", "",
    ...report.insight.inferences.map((item) => {
      const basis = item.basisFactIds.map((id) => facts.get(id)).filter((fact) => fact !== undefined)
        .map((fact) => `${fact.text}（${fact.sourceUrl}，观测于 ${fact.observedAt}）`).join("；");
      return `- ${item.text}${basis ? `\n  - 依据：${basis}` : ""}`;
    }),
    "", "## 仍待确认", "",
    ...report.insight.unknowns.map((item) => `- ${item.text}`),
    "", "## 来源记录", "",
    ...report.snapshots.map((item) => `- ${sourceNames.get(item.sourceId) ?? "关注公司"}｜${item.title}｜${jobStatus(item.status)}｜${item.location}\n  - 职责：${item.dutyExcerpt}\n  - 要求：${item.requirementExcerpt}\n  - 来源：${item.sourceUrl}\n  - 观测于 ${item.observedAt}`),
  ];
  return `${lines.join("\n").trim()}\n`;
}

function downloadMarkdown(report: PanoramaReport, content: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `全景分析-第${report.insight.versionNumber}版-${report.insight.createdAt.slice(0, 10)}.md`;
  anchor.hidden = true;
  document.body.append(anchor);
  try { anchor.click(); }
  finally { anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 0); }
}

export function HrPanoramaReport({ report, comparison = { state: "none", currentSourceFailures: {} }, onCopy = copyVisibleText }: { report: PanoramaReport; comparison?: HrPanoramaComparison; onCopy?: (text: string) => Promise<boolean> }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const [view, setView] = useState<ReportView>("overview");
  const [companyFilter, setCompanyFilter] = useState("all");
  const [trackFilter, setTrackFilter] = useState<RecruitmentTrack | "all">("all");
  const [locationFilter, setLocationFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<HrPanoramaSnapshot["status"] | "all">("all");
  const [directionFilter, setDirectionFilter] = useState("all");
  const sourceById = new Map(report.sources.map((source) => [source.sourceId, source]));
  const factById = new Map(report.insight.facts.map((fact) => [fact.factId, fact]));
  const openJobs = report.snapshots.filter((item) => item.status === "open").length;
  const geography = countBy(report.snapshots, (item) => item.location);
  const changes = recruitmentChanges(report, comparison);
  const markdown = formatHrPanoramaReportMarkdown(report, comparison);
  const locations = [...new Set(report.snapshots.map((item) => item.location))].sort((left, right) => left.localeCompare(right, "zh-CN"));
  const directions = [...new Set(report.snapshots.map(technicalDirection))].sort((left, right) => left.localeCompare(right, "zh-CN"));
  const filteredJobs = report.snapshots.filter((item) => (companyFilter === "all" || item.sourceId === companyFilter)
    && (trackFilter === "all" || recruitmentTrack(item) === trackFilter)
    && (locationFilter === "all" || item.location === locationFilter)
    && (statusFilter === "all" || item.status === statusFilter)
    && (directionFilter === "all" || technicalDirection(item) === directionFilter));
  useEffect(() => {
    setCopyState("idle"); setView("overview"); setCompanyFilter("all"); setTrackFilter("all");
    setLocationFilter("all"); setStatusFilter("all"); setDirectionFilter("all");
  }, [report.insight.insightVersionId]);
  useEffect(() => {
    if (copyState === "idle") return;
    const timer = window.setTimeout(() => setCopyState("idle"), 1800);
    return () => window.clearTimeout(timer);
  }, [copyState]);
  const copy = async () => {
    try { setCopyState(await onCopy(markdown) ? "copied" : "error"); }
    catch { setCopyState("error"); }
  };
  return <article className="hr-panorama-report" data-report-id={report.insight.insightVersionId}>
    <header className="hr-panorama-report-cover">
      <div className="hr-panorama-report-meta">
        <span>全景分析 · 第 {report.insight.versionNumber} 版</span>
        <div><time dateTime={report.insight.createdAt}>分析于 {time(report.insight.createdAt)}</time><span className="hr-panorama-report-actions"><button onClick={() => void copy()} type="button">{copyState === "copied" ? "已复制报告" : copyState === "error" ? "复制失败，请重试" : "复制报告"}</button><button onClick={() => downloadMarkdown(report, markdown)} type="button">下载报告</button></span></div>
      </div>
      <div className="hr-panorama-report-lead">
        <p>公开招聘情报</p>
        <h1>{report.insight.summary}</h1>
        <div className="hr-panorama-company-pills">{report.sources.map((source) => <span key={source.sourceId}>{source.canonicalName}</span>)}</div>
      </div>
      <dl className="hr-panorama-report-stats">
        <div><dt>覆盖公司</dt><dd>{report.sources.length}</dd></div>
        <div><dt>公开岗位记录</dt><dd>{report.snapshots.length}</dd></div>
        <div><dt>仍在公开招聘</dt><dd>{openJobs}</dd></div>
        <div><dt>公开事实</dt><dd>{report.insight.facts.length}</dd></div>
      </dl>
    </header>

    <nav aria-label="全景分析视图" className="hr-panorama-report-tabs">
      {REPORT_VIEWS.map((item) => <button aria-selected={view === item.id} key={item.id} onClick={() => setView(item.id)} role="tab" type="button">{item.label}</button>)}
    </nav>

    {view === "social" && <TrackView comparison={comparison} report={report} sourceById={sourceById} track="social" />}
    {view === "campus" && <TrackView comparison={comparison} report={report} sourceById={sourceById} track="campus" />}

    <section className="hr-panorama-signal-grid" aria-label="招聘信号概览" hidden={view !== "overview"}>
      <section>
        <header><span>01</span><h2>研发方向</h2></header>
        {Object.keys(report.insight.directionClusters).length ? <dl>{Object.entries(report.insight.directionClusters).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{clusterValue(value)}</dd></div>)}</dl> : <p>公开信息尚不足以形成方向聚类。</p>}
      </section>
      <section>
        <header><span>02</span><h2>招聘变化</h2></header>
        {changes ? <><dl><div><dt>新增岗位</dt><dd>{changes.added}</dd></div><div><dt>明确关闭</dt><dd>{changes.removed}</dd></div><div><dt>持续招聘</dt><dd>{changes.continued}</dd></div><div><dt>本次未再次采集到</dt><dd>{changes.unobserved}</dd></div></dl><p>“本次未再次采集到”仍待验证，不代表停止招聘。</p>{changes.failedSourceIds.length > 0 && <p>{changes.failedSourceIds.map((id) => sourceById.get(id)?.canonicalName ?? "关注公司").join("、")}本轮采集失败，无法判断变化。</p>}</> : <><p>{comparisonMessage(comparison)}</p>{currentFailureIds(comparison).map((id) => <p key={id}>{sourceById.get(id)?.canonicalName ?? "关注公司"}本轮采集失败，无法判断变化。</p>)}</>}
      </section>
      <section>
        <header><span>03</span><h2>地域分布</h2></header>
        {geography.length ? <dl>{geography.map(([location, count]) => <div key={location}><dt>{location}</dt><dd>{count} 个岗位</dd></div>)}</dl> : <p>本版没有可用的岗位地点。</p>}
      </section>
      <section>
        <header><span>04</span><h2>关键能力</h2></header>
        {report.snapshots.length ? <ul>{report.snapshots.slice(0, 6).map((item) => <li key={item.snapshotId}><strong>{item.title}</strong><span>{item.requirementExcerpt}</span></li>)}</ul> : <p>本版没有可核验的岗位能力要求。</p>}
      </section>
      <section>
        <header><span>05</span><h2>重点团队与投入信号</h2></header>
        {report.insight.inferences.length ? <ul>{report.insight.inferences.slice(0, 4).map((item, index) => <li key={`${index}:${item.text}`}><strong>AI 推断</strong><span>{item.text}</span></li>)}</ul> : <p>公开事实尚不足以判断重点团队或研发资源投入。</p>}
      </section>
    </section>

    {view === "strategy" && <section className="hr-panorama-strategy" data-report-view="strategy">
      <header><p>PRODUCT &amp; BUSINESS SIGNALS</p><h2>产品与业务方向</h2><span>方向判断来自公开岗位事实；推断与事实严格分开。</span></header>
      <div className="hr-panorama-strategy-grid">
        <section><h3>研发资源方向</h3>{Object.keys(report.insight.directionClusters).length
          ? <dl>{Object.entries(report.insight.directionClusters).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{clusterValue(value)}</dd></div>)}</dl>
          : <p>公开信息尚不足以形成方向聚类。</p>}</section>
        <section><h3>业务投入信号</h3>{report.insight.inferences.length
          ? <ul>{report.insight.inferences.map((item, index) => <li key={`${index}:${item.text}`}>{item.text}</li>)}</ul>
          : <p>证据不足，暂不做业务方向推断。</p>}</section>
      </div>
      {report.insight.unknowns.length > 0 && <aside><strong>仍待确认</strong><span>{report.insight.unknowns.map((item) => item.text).join("；")}</span></aside>}
    </section>}

    {view === "jobs" && <section className="hr-panorama-job-view" data-report-view="jobs">
      <header><div><p>PUBLIC JOB RECORDS</p><h2>岗位明细</h2></div><strong>{filteredJobs.length} / {report.snapshots.length} 条</strong></header>
      <div className="hr-panorama-job-filters">
        <label><span>公司</span><select aria-label="公司" onChange={(event) => setCompanyFilter(event.target.value)} value={companyFilter}><option value="all">全部公司</option>{report.sources.map((source) => <option key={source.sourceId} value={source.sourceId}>{source.canonicalName}</option>)}</select></label>
        <label><span>招聘类型</span><select aria-label="招聘类型" onChange={(event) => setTrackFilter(event.target.value as RecruitmentTrack | "all")} value={trackFilter}><option value="all">全部类型</option><option value="social">社招</option><option value="campus">校招/实习</option><option value="unknown">待分类</option></select></label>
        <label><span>地点</span><select aria-label="地点" onChange={(event) => setLocationFilter(event.target.value)} value={locationFilter}><option value="all">全部地点</option>{locations.map((location) => <option key={location} value={location}>{location}</option>)}</select></label>
        <label><span>岗位状态</span><select aria-label="岗位状态" onChange={(event) => setStatusFilter(event.target.value as HrPanoramaSnapshot["status"] | "all")} value={statusFilter}><option value="all">全部状态</option><option value="open">招聘中</option><option value="closed">已关闭</option><option value="unknown">待确认</option></select></label>
        <label><span>技术方向</span><select aria-label="技术方向" onChange={(event) => setDirectionFilter(event.target.value)} value={directionFilter}><option value="all">全部方向</option>{directions.map((direction) => <option key={direction} value={direction}>{direction}</option>)}</select></label>
      </div>
      <JobCards items={filteredJobs} sourceById={sourceById} />
    </section>}

    <section className="hr-panorama-evidence" data-evidence-kind="facts" hidden={view !== "evidence"}>
      <header><p>FACTS</p><h2>公开事实</h2><span>以下内容可回到原始公开页面核验。</span></header>
      <ol>{report.insight.facts.map((fact) => <li id={`panorama-fact-${encodeURIComponent(fact.factId)}`} key={fact.factId}>
        <p>{fact.text}</p>
        <footer><a href={fact.sourceUrl} rel="noreferrer" target="_blank">查看公开来源 ↗</a><time dateTime={fact.observedAt}>观测于 {time(fact.observedAt)}</time></footer>
      </li>)}</ol>
    </section>

    <section className="hr-panorama-evidence is-inference" data-evidence-kind="inferences" hidden={view !== "evidence"}>
      <header><p>INFERENCES</p><h2>AI 推断</h2><span>推断不是公开事实，并列出其事实依据。</span></header>
      {report.insight.inferences.length ? <ol>{report.insight.inferences.map((item, index) => <li key={`${index}:${item.text}`}>
        <p>{item.text}</p><div className="hr-panorama-inference-basis">{item.basisFactIds.map((factId) => factById.get(factId)).filter((fact) => fact !== undefined).map((fact) => <span key={fact.factId}><a href={fact.sourceUrl} rel="noreferrer" target="_blank">{fact.text} ↗</a><time dateTime={fact.observedAt}>观测于 {time(fact.observedAt)}</time></span>)}</div>
      </li>)}</ol> : <p className="hr-panorama-empty-copy">本版没有发布 AI 推断。</p>}
    </section>

    <section className="hr-panorama-evidence is-unknown" data-evidence-kind="unknowns" hidden={view !== "evidence"}>
      <header><p>UNKNOWNS</p><h2>仍待确认</h2><span>公开材料没有证明的事项，不作为否定结论。</span></header>
      {report.insight.unknowns.length ? <ul>{report.insight.unknowns.map((item, index) => <li key={`${index}:${item.text}`}>{item.text}</li>)}</ul> : <p className="hr-panorama-empty-copy">本版没有额外未知项。</p>}
    </section>

    <section className="hr-panorama-sources" hidden={view !== "evidence"}>
      <header><p>SOURCES</p><h2>来源记录</h2><span>每条岗位记录保留公开链接和实际观测时间。</span></header>
      {report.snapshots.length ? <div>{report.snapshots.map((item) => <article key={item.snapshotId}>
        <span>{sourceById.get(item.sourceId)?.canonicalName ?? "关注公司"}</span><h3>{item.title}</h3><p>{item.location} · {item.dutyExcerpt}</p>
        <footer><a href={item.sourceUrl} rel="noreferrer" target="_blank">打开岗位来源 ↗</a><time dateTime={item.observedAt}>{time(item.observedAt)}</time></footer>
      </article>)}</div> : <p className="hr-panorama-empty-copy">本版没有可展示的岗位快照。</p>}
    </section>

    <details className="hr-panorama-diagnostics">
      <summary>高级诊断</summary>
      <dl><div><dt>分析版本</dt><dd>{report.insight.insightVersionId}</dd></div><div><dt>执行记录</dt><dd>{report.insight.sourceConversationId}</dd></div><div><dt>模型版本</dt><dd>{report.insight.modelVersion}</dd></div><div><dt>生成 Agent</dt><dd>{report.insight.agentId}</dd></div></dl>
    </details>
  </article>;
}
