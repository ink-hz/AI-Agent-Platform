import { useEffect, useState } from "react";

import { platformPath } from "../../auth";
import { copyVisibleText } from "../../clipboard";
import type { HrPanoramaReport as PanoramaReport, HrPanoramaSnapshot, HrPanoramaV2Dimensions } from "../../hrPanoramaTypes";

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

function v2Dimensions(report: PanoramaReport): HrPanoramaV2Dimensions | null {
  const value = report.insight.directionClusters._v2;
  return value && typeof value === "object" && "schema_version" in value && value.schema_version === 2
    ? value as HrPanoramaV2Dimensions : null;
}

function legacyDirections(report: PanoramaReport): Array<[string, number]> {
  return Object.entries(report.insight.directionClusters)
    .filter((entry): entry is [string, number] => entry[0] !== "_v2" && typeof entry[1] === "number");
}

const TRACK_LABELS: Record<string, string> = { social: "社招", campus: "校招", intern: "实习", unknown: "待分类" };
const SENIORITY_LABELS: Record<string, string> = { senior: "高级 / 专家", mid: "中级", junior: "初级", graduate: "应届 / 实习", unspecified: "未明确" };
const FAMILY_LABELS: Record<string, string> = {
  research_development: "研发", quality: "质量", manufacturing: "制造工艺", supply_chain: "供应链",
  product: "产品", sales_marketing: "销售与市场", operations: "运营与交付", corporate: "职能", other: "其他",
};

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

function collectionFailureLabel(errorCode: string): string {
  const labels: Record<string, string> = {
    search_unavailable: "检索通道本轮未完成",
    source_timeout: "来源响应超时",
    source_rejected: "来源拒绝访问",
    unsupported_schema: "来源结构暂未适配",
    source_unavailable: "来源暂时不可访问",
  };
  return labels[errorCode] ?? "来源读取未完成";
}

function currentFailureIds(comparison: HrPanoramaComparison): string[] {
  return comparison.state === "available" || comparison.state === "none" ? Object.keys(comparison.currentSourceFailures) : [];
}

type ReportView = "overview" | "social" | "campus" | "intern" | "strategy" | "jobs" | "evidence";
type RecruitmentTrack = "social" | "campus" | "intern" | "unknown";

const REPORT_VIEWS: Array<{ id: ReportView; label: string }> = [
  { id: "overview", label: "AI 分析" },
  { id: "social", label: "社招" },
  { id: "campus", label: "校招" },
  { id: "intern", label: "实习" },
  { id: "strategy", label: "产品与业务方向" },
  { id: "jobs", label: "原始岗位数据" },
  { id: "evidence", label: "来源证据" },
];

function recruitmentTrack(item: HrPanoramaSnapshot): RecruitmentTrack {
  const text = `${item.title} ${item.dutyExcerpt} ${item.requirementExcerpt}`.toLocaleLowerCase("zh-CN");
  const url = item.sourceUrl.toLocaleLowerCase("zh-CN");
  if (/^https:\/\/kwh0jtf778\.jobs\.feishu\.cn\/index(?:\/|$)/i.test(url)
    || /^https:\/\/[^/]+\.zhiye\.com\/$/i.test(url)
    || /^https:\/\/www\.elegoo\.com\.cn\/index\/join\//i.test(url)
    || /(?:^|[/_.-])(?:social(?:eng|[-_/]?recruitment)?|experienced|professional-hire)(?:$|[/_.?&#-])|\/gwtd1(?:-\d+)?\.html$/i.test(url)) return "social";
  if (/^https:\/\/kwh0jtf778\.jobs\.feishu\.cn\/229043(?:\/|$)/i.test(url)
    || /(?:^|[/_.-])(?:campus|graduate)(?:[-_/]?recruitment)?(?:$|[/_.?&#-])|\/gwtd(?:-\d+)?\.html$/i.test(url)) return "campus";
  if (/^https:\/\/kwh0jtf778\.jobs\.feishu\.cn\/073183(?:\/|$)/i.test(url)
    || /(?:^|[/_.-])intern(?:ship|recruitment)?(?:$|[/_.?&#-])/i.test(url)) return "intern";
  if (/(实习|(?<![a-z])intern(?:ship)?(?![a-z]))/i.test(text)) return "intern";
  if (/(校招|校园招聘|应届|毕业生|\d{2}届|(?<![a-z])(?:campus|graduate)(?![a-z]))/i.test(text)) return "campus";
  if (/(社招|社会招聘|社会人才|(?<![a-z])experienced(?![a-z])|professional-hire)/i.test(text)) return "social";
  return "unknown";
}

const TECHNICAL_DIRECTION_PATTERNS: Array<[string, RegExp]> = [
  ["光学", /(光学|镜头|成像|光机|光电|zemax|code\s*v)/i],
  ["硬件", /(硬件|电子|电路|pcb|pcba|emc|ems|esd|fpga|soc|芯片|射频)/i],
  ["结构", /(结构|机械|机电|模具|公差|cad|cae|solidworks)/i],
  ["软件", /(软件|前端|后端|客户端|嵌入式|固件|操作系统|java|c\+\+|python|golang)/i],
  ["算法", /(算法|人工智能|机器学习|深度学习|计算机视觉|点云|slam|标定|(?<![a-z])ai(?![a-z]))/i],
  ["制造工艺", /(制造|工艺|生产|量产|试产|装配|注塑|钣金|cnc|良率)/i],
  ["质量", /(质量|测试|可靠性|(?<![a-z])(?:dqe|sqe|qe)(?![a-z])|失效分析|认证)/i],
  ["产品", /(产品经理|产品规划|产品设计|需求分析|用户体验|ux|id设计)/i],
  ["供应链", /(供应链|采购|计划|pmc|物流|物料)/i],
];

const SUPPLEMENTAL_TECHNICAL_DIRECTION_PATTERNS: Array<[string, RegExp]> = [
  ["光学", /(光学|镜头|成像|光机|光电|zemax|code\s*v)/i],
  ["硬件", /(硬件|电子|电路|pcb|pcba|emc|esd|fpga|芯片|射频)/i],
  ["结构", /(结构设计|机械设计|模具|公差|solidworks|creo|catia)/i],
  ["软件", /(嵌入式|固件|操作系统|java|c\+\+|python|golang)/i],
  ["算法", /(算法|机器学习|深度学习|计算机视觉|点云|slam|标定)/i],
  ["制造工艺", /(制造工艺|生产工艺|装配工艺|注塑|钣金|cnc|良率)/i],
  ["质量", /(可靠性|(?<![a-z])(?:dqe|sqe|qe)(?![a-z])|失效分析|认证)/i],
  ["产品", /(产品规划|产品经理|用户体验|ux|id设计)/i],
  ["供应链", /(供应链|采购|pmc|物流|物料)/i],
];

function technicalDirections(item: HrPanoramaSnapshot): string[] {
  const title = item.title.toLocaleLowerCase("zh-CN");
  const fromTitle = TECHNICAL_DIRECTION_PATTERNS.filter(([, pattern]) => pattern.test(title)).map(([label]) => label);
  const text = `${item.title} ${item.dutyExcerpt} ${item.requirementExcerpt}`.toLocaleLowerCase("zh-CN");
  if (fromTitle.length) {
    const supplemental = SUPPLEMENTAL_TECHNICAL_DIRECTION_PATTERNS
      .filter(([label, pattern]) => !fromTitle.includes(label) && pattern.test(text))
      .map(([label]) => label);
    return [...fromTitle, ...supplemental];
  }
  const fromText = TECHNICAL_DIRECTION_PATTERNS.filter(([, pattern]) => pattern.test(text)).map(([label]) => label);
  return fromText.length ? fromText : ["其他"];
}

function technicalDirection(item: HrPanoramaSnapshot): string {
  return technicalDirections(item)[0];
}

function sourceChannel(url: string): string {
  const value = url.toLocaleLowerCase("zh-CN");
  if (/(campus|xyzp|xiaozhao|校招)/i.test(value)) return "校招入口";
  if (/(?:^|[/_.-])intern(?:ship|recruitment)?(?:$|[/_.?&#-])|073183|实习/i.test(value)) return "实习入口";
  if (/(experienced|social|shzp)/i.test(value)) return "社招入口";
  if (/(jobs\.feishu|zhiye\.com|hr\.|jobs\.)/i.test(value)) return "招聘系统";
  if (/(zhaopin|nowcoder|career\.|job\.)/i.test(value)) return "公开招聘补充";
  return "公司官网";
}

function channelSnapshots(source: PanoramaReport["sources"][number], snapshots: HrPanoramaSnapshot[]): Map<string, HrPanoramaSnapshot[]> {
  const channels = new Map(source.approvedUrls.map((url) => [url, [] as HrPanoramaSnapshot[]]));
  for (const snapshot of snapshots.filter((item) => item.sourceId === source.sourceId)) {
    const matches = source.approvedUrls.filter((url) => snapshot.sourceUrl === url || snapshot.sourceUrl.startsWith(`${url.replace(/\/$/, "")}/`));
    const selected = matches.sort((left, right) => right.length - left.length)[0];
    if (selected) channels.get(selected)?.push(snapshot);
  }
  return channels;
}

function JobCards({ items, sourceById }: { items: HrPanoramaSnapshot[]; sourceById: Map<string, PanoramaReport["sources"][number]> }) {
  if (!items.length) return <p className="hr-panorama-empty-copy">本版没有可展示的匹配岗位记录。</p>;
  return <div className="hr-panorama-job-cards">{items.map((item) => <article key={item.snapshotId}>
    <span>{sourceById.get(item.sourceId)?.canonicalName ?? "关注公司"}</span><h3>{item.title}</h3><p>{item.location} · {item.dutyExcerpt}</p>
    <footer><a href={item.sourceUrl} rel="noreferrer" target="_blank">打开岗位来源 ↗</a><time dateTime={item.observedAt}>{time(item.observedAt)}</time></footer>
  </article>)}</div>;
}

function TrackView({ track, report, comparison, sourceById }: { track: Exclude<RecruitmentTrack, "unknown">; report: PanoramaReport; comparison: HrPanoramaComparison; sourceById: Map<string, PanoramaReport["sources"][number]> }) {
  const label = TRACK_LABELS[track];
  const items = report.snapshots.filter((item) => recruitmentTrack(item) === track);
  const unknown = report.snapshots.filter((item) => recruitmentTrack(item) === "unknown");
  const failures = new Set(currentFailureIds(comparison));
  return <section className="hr-panorama-track-view" data-report-view={track}>
    <header><div><p>{track === "social" ? "SOCIAL RECRUITING" : track === "campus" ? "CAMPUS RECRUITING" : "INTERNSHIPS"}</p><h2>{label}{track === "intern" ? "岗位" : "招聘"}</h2></div><strong>{items.length} 个可判定岗位</strong></header>
    <div className="hr-panorama-track-coverage">{report.sources.map((source) => {
      const count = items.filter((item) => item.sourceId === source.sourceId).length;
      const hasUnknown = unknown.some((item) => item.sourceId === source.sourceId);
      return <article key={source.sourceId}><strong>{source.canonicalName}</strong><span>{failures.has(source.sourceId)
        ? "本版来源失败，无法判断"
        : count > 0 ? `${count} 个公开岗位`
          : hasUnknown ? `未识别到${label}标记，待确认` : `本轮没有可判定的${label}记录，待确认`}</span></article>;
    })}</div>
    {unknown.length > 0 && <p className="hr-panorama-track-warning">{unknown.length} 个岗位尚未识别招聘类型，未强行归入社招、校招或实习。</p>}
    {items.length > 200 && <p className="hr-panorama-track-warning">页面先展示前 200 条；完整数据可在“原始岗位数据”筛选或下载 Excel。</p>}
    <JobCards items={items.slice(0, 200)} sourceById={sourceById} />
  </section>;
}

export function formatHrPanoramaReportMarkdown(
  report: PanoramaReport,
  comparison: HrPanoramaComparison = { state: "none", currentSourceFailures: {} },
  selectedSnapshots: HrPanoramaSnapshot[] = report.snapshots,
  filtered = false,
): string {
  const changes = filtered ? null : recruitmentChanges(report, comparison);
  const selectedSnapshotIds = new Set(selectedSnapshots.map((item) => item.snapshotId));
  const selectedSourceIds = new Set(selectedSnapshots.map((item) => item.sourceId));
  const selectedSources = filtered ? report.sources.filter((source) => selectedSourceIds.has(source.sourceId)) : report.sources;
  const selectedFacts = filtered ? report.insight.facts.filter((fact) => selectedSnapshotIds.has(fact.snapshotId)) : report.insight.facts;
  const selectedFactIds = new Set(selectedFacts.map((fact) => fact.factId));
  const selectedInferences = filtered ? report.insight.inferences.filter((item) => item.basisFactIds.every((id) => selectedFactIds.has(id))) : report.insight.inferences;
  const selectedUnknowns = filtered ? [] : report.insight.unknowns;
  const facts = new Map(selectedFacts.map((fact) => [fact.factId, fact]));
  const sourceNames = new Map(report.sources.map((source) => [source.sourceId, source.canonicalName]));
  const observationTimes = selectedSnapshots.map((item) => item.observedAt).sort();
  const asOf = observationTimes[observationTimes.length - 1] ?? report.insight.createdAt;
  const geography = countBy(selectedSnapshots, (item) => item.location);
  const directions = filtered ? countBy(selectedSnapshots, technicalDirection) : legacyDirections(report);
  const dimensions = filtered ? null : v2Dimensions(report);
  const structureLines = dimensions ? [
    "", "## 招聘结构", "",
    ...Object.entries(dimensions.tracks).map(([key, value]) => `- ${TRACK_LABELS[key] ?? key}：${value}`),
    "", "### 岗位族", "",
    ...Object.entries(dimensions.job_families).map(([key, value]) => `- ${FAMILY_LABELS[key] ?? key}：${value}`),
    "", "### 资历结构", "",
    ...Object.entries(dimensions.seniority).map(([key, value]) => `- ${SENIORITY_LABELS[key] ?? key}：${value}`),
    "", "### 显式技术栈", "",
    ...(dimensions.skills.length ? dimensions.skills.map((item) => `- ${item.name}：${item.job_count}`) : ["- 暂无可核验的显式技术栈。"]),
    "", "## 公司 × 技术方向", "",
    ...selectedSources.map((source) => {
      const metrics = dimensions.company_matrix[source.sourceId];
      const directionText = metrics ? Object.entries(metrics.directions).filter(([, value]) => value > 0).map(([key, value]) => `${key} ${value}`).join("、") : "无岗位证据";
      return `- ${source.canonicalName}：${metrics?.job_count ?? 0} 个岗位；${directionText || "未识别技术方向"}`;
    }),
    "", "## 分析边界", "",
    `- ${dimensions.trend.message}`,
    ...dimensions.interpretation_limits.map((item) => `- ${item}`),
  ] : [];
  const jobStatus = (status: HrPanoramaSnapshot["status"]) => status === "open" ? "公开招聘中" : status === "closed" ? "已下线" : "状态待确认";
  const lines = [
    `# 全景分析 · 第 ${report.insight.versionNumber} 版`, "",
    filtered ? `筛选结果：${selectedSnapshots.length} 条岗位，覆盖 ${selectedSources.length} 家公司。` : report.insight.summary, "",
    `- 覆盖公司：${selectedSources.map((source) => source.canonicalName).join("、") || "无匹配公司"}`,
    `- 分析时间：${report.insight.createdAt}`,
    `- 观测截至：${asOf}`,
    "", "## 研发方向", "",
    ...directions.map(([label, value]) => `- ${label}：${clusterValue(value)}`),
    "", "## 招聘变化", "",
    ...(filtered ? ["- 已按当前筛选范围导出岗位；招聘变化请查看完整报告。"] : changes ? [
      `- 新增岗位：${changes.added}`, `- 明确关闭：${changes.removed}`, `- 持续招聘：${changes.continued}`,
      `- 本版未再次观测到（待验证，不代表停止招聘）：${changes.unobserved}`,
      ...(changes.failedSourceIds.length ? [`- ${changes.failedSourceIds.map((id) => sourceNames.get(id) ?? "关注公司").join("、")}本版来源失败，无法判断变化。`] : []),
    ] : [comparisonMessage(comparison), ...currentFailureIds(comparison).map((id) => `- ${sourceNames.get(id) ?? "关注公司"}本版来源失败，无法判断变化。`)]),
    "", "## 地域分布", "",
    ...(geography.length ? geography.map(([location, count]) => `- ${location}：${count} 个岗位`) : ["暂无可用岗位地点。"]),
    "", "## 关键能力", "",
    ...(selectedSnapshots.length ? selectedSnapshots.map((item) => `- ${sourceNames.get(item.sourceId) ?? "关注公司"}｜${item.title}：${item.requirementExcerpt}`) : ["暂无可核验的岗位能力要求。"]),
    ...structureLines,
    "", "## 公开事实", "",
    ...selectedFacts.map((fact) => `- ${fact.text}\n  - 来源：${fact.sourceUrl}\n  - 观测于 ${fact.observedAt}`),
    "", "## AI 推断", "",
    ...selectedInferences.map((item) => {
      const basis = item.basisFactIds.map((id) => facts.get(id)).filter((fact) => fact !== undefined)
        .map((fact) => `${fact.text}（${fact.sourceUrl}，观测于 ${fact.observedAt}）`).join("；");
      return `- ${item.text}${basis ? `\n  - 依据：${basis}` : ""}`;
    }),
    "", "## 仍待确认", "",
    ...selectedUnknowns.map((item) => `- ${item.text}`),
    "", "## 来源记录", "",
    ...selectedSnapshots.map((item) => `- ${sourceNames.get(item.sourceId) ?? "关注公司"}｜${item.title}｜${jobStatus(item.status)}｜${item.location}\n  - 职责：${item.dutyExcerpt}\n  - 要求：${item.requirementExcerpt}\n  - 来源：${item.sourceUrl}\n  - 观测于 ${item.observedAt}`),
  ];
  return `${lines.join("\n").trim()}\n`;
}

function coverageLabel(state: PanoramaReport["publication"]["coverageState"]): string {
  return {
    succeeded: "来源已核验",
    empty_confirmed: "已核验，本版未发现公开岗位",
    partial: "部分来源可用",
    failed: "来源读取失败",
    not_observed: "本版未观测",
  }[state];
}

export function HrPanoramaReport({ report, comparison = { state: "none", currentSourceFailures: {} }, onCopy = copyVisibleText }: { report: PanoramaReport; comparison?: HrPanoramaComparison; onCopy?: (text: string) => Promise<boolean> }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const [view, setView] = useState<ReportView>("overview");
  const [companyFilter, setCompanyFilter] = useState("all");
  const [trackFilter, setTrackFilter] = useState<RecruitmentTrack | "all">("all");
  const [locationFilter, setLocationFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<HrPanoramaSnapshot["status"] | "all">("all");
  const [directionFilter, setDirectionFilter] = useState("all");
  const [jobLimit, setJobLimit] = useState(100);
  const sourceById = new Map(report.sources.map((source) => [source.sourceId, source]));
  const coverageById = new Map(report.publication.sourceCoverage.map((item) => [item.sourceId, item]));
  const factById = new Map(report.insight.facts.map((fact) => [fact.factId, fact]));
  const openJobs = report.snapshots.filter((item) => item.status === "open").length;
  const geography = countBy(report.snapshots, (item) => item.location);
  const changes = recruitmentChanges(report, comparison);
  const dimensions = v2Dimensions(report);
  const directionEntries = legacyDirections(report);
  const markdown = formatHrPanoramaReportMarkdown(report, comparison);
  const locations = [...new Set(report.snapshots.map((item) => item.location))].sort((left, right) => left.localeCompare(right, "zh-CN"));
  const directions = [...new Set(report.snapshots.flatMap(technicalDirections))].sort((left, right) => left.localeCompare(right, "zh-CN"));
  const filteredJobs = report.snapshots.filter((item) => (companyFilter === "all" || item.sourceId === companyFilter)
    && (trackFilter === "all" || recruitmentTrack(item) === trackFilter)
    && (locationFilter === "all" || item.location === locationFilter)
    && (statusFilter === "all" || item.status === statusFilter)
    && (directionFilter === "all" || technicalDirections(item).includes(directionFilter)));
  const exportPath = (format: "pdf" | "xlsx" | "md") => platformPath(
    `/api/hr/panorama/reports/${encodeURIComponent(report.publication.bundleId)}/export?format=${format}`,
  );
  useEffect(() => {
    setCopyState("idle"); setView("overview"); setCompanyFilter("all"); setTrackFilter("all");
    setLocationFilter("all"); setStatusFilter("all"); setDirectionFilter("all");
    setJobLimit(100);
  }, [report.publication.publicationId]);
  useEffect(() => { setJobLimit(100); }, [companyFilter, trackFilter, locationFilter, statusFilter, directionFilter]);
  useEffect(() => {
    if (copyState === "idle") return;
    const timer = window.setTimeout(() => setCopyState("idle"), 1800);
    return () => window.clearTimeout(timer);
  }, [copyState]);
  const copy = async () => {
    try { setCopyState(await onCopy(markdown) ? "copied" : "error"); }
    catch { setCopyState("error"); }
  };
  return <article className="hr-panorama-report" data-report-id={report.publication.publicationId}>
    <header className="hr-panorama-report-cover">
      <div className="hr-panorama-report-meta">
        <span>已发布招聘情报 · {coverageLabel(report.publication.coverageState)}</span>
        <div><time dateTime={report.publication.generatedAt}>数据截至 {time(report.publication.generatedAt)}</time><span className="hr-panorama-report-actions"><button onClick={() => void copy()} type="button">{copyState === "copied" ? "已复制报告" : copyState === "error" ? "复制失败" : "复制报告"}</button><a download href={exportPath("pdf")}>下载 PDF</a><a download href={exportPath("xlsx")}>下载 Excel</a><a download href={exportPath("md")}>下载 Markdown</a></span></div>
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

    <nav aria-label="全景分析视图" className="hr-panorama-report-tabs" role="tablist">
      {REPORT_VIEWS.map((item) => <button aria-selected={view === item.id} key={item.id} onClick={() => setView(item.id)} role="tab" tabIndex={view === item.id ? 0 : -1} type="button">{item.label}</button>)}
    </nav>

    {view === "social" && <TrackView comparison={comparison} report={report} sourceById={sourceById} track="social" />}
    {view === "campus" && <TrackView comparison={comparison} report={report} sourceById={sourceById} track="campus" />}
    {view === "intern" && <TrackView comparison={comparison} report={report} sourceById={sourceById} track="intern" />}

    <section className="hr-panorama-signal-grid" aria-label="招聘信号概览" hidden={view !== "overview"}>
      <section>
        <header><span>01</span><h2>研发方向</h2></header>
        {directionEntries.length ? <dl>{directionEntries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{clusterValue(value)}</dd></div>)}</dl> : <p>公开信息尚不足以形成方向聚类。</p>}
      </section>
      <section>
        <header><span>02</span><h2>招聘变化</h2></header>
        {changes ? <><dl><div><dt>新增岗位</dt><dd>{changes.added}</dd></div><div><dt>明确关闭</dt><dd>{changes.removed}</dd></div><div><dt>持续招聘</dt><dd>{changes.continued}</dd></div><div><dt>本版未再次观测到</dt><dd>{changes.unobserved}</dd></div></dl><p>“本版未再次观测到”仍待验证，不代表停止招聘。</p>{changes.failedSourceIds.length > 0 && <p>{changes.failedSourceIds.map((id) => sourceById.get(id)?.canonicalName ?? "关注公司").join("、")}本版来源失败，无法判断变化。</p>}</> : <><p>{comparisonMessage(comparison)}</p>{currentFailureIds(comparison).map((id) => <p key={id}>{sourceById.get(id)?.canonicalName ?? "关注公司"}本版来源失败，无法判断变化。</p>)}</>}
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
      <section>
        <header><span>06</span><h2>公开事实与来源</h2></header>
        {report.insight.facts.length ? <ul>{report.insight.facts.slice(0, 4).map((fact) => <li key={fact.factId}><strong>公开事实</strong><span>{fact.text}</span><a href={fact.sourceUrl} rel="noreferrer" target="_blank">查看公开来源 ↗</a></li>)}</ul> : <p>本版没有可展示的公开事实。</p>}
      </section>
    </section>

    {view === "overview" && dimensions && <section className="hr-panorama-v2" aria-label="招聘结构分析">
      <header><div><p>CODE-COMPILED INTELLIGENCE</p><h2>公司 × 技术方向</h2></div><span>{dimensions.scope.unique_job_count} 个去重岗位 · {dimensions.scope.duplicate_snapshot_count} 个重复观测已剔除</span></header>
      <div className="hr-panorama-v2-table-wrap"><table><thead><tr><th>公司</th><th>岗位</th>{Object.keys(dimensions.directions).filter((key) => key !== "其他").map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>
        {report.sources.map((source) => { const metrics = dimensions.company_matrix[source.sourceId]; const coverage = coverageById.get(source.sourceId); const unavailable = coverage?.state === "failed" || coverage?.state === "not_observed"; return <tr key={source.sourceId}><th>{source.canonicalName}<small>{coverage ? coverageLabel(coverage.state) : "本版未观测"}</small></th><td>{unavailable ? "—" : metrics?.job_count ?? 0}</td>{Object.keys(dimensions.directions).filter((key) => key !== "其他").map((key) => <td key={key}>{unavailable ? "—" : metrics?.directions[key] ?? 0}</td>)}</tr>; })}
      </tbody></table></div>
      <div className="hr-panorama-v2-grid">
        <section><h3>社招、校招与实习</h3><dl>{Object.entries(dimensions.tracks).map(([key, value]) => <div key={key}><dt>{TRACK_LABELS[key] ?? key}</dt><dd>{value}</dd></div>)}</dl></section>
        <section><h3>资历与能力结构</h3><dl>{Object.entries(dimensions.seniority).map(([key, value]) => <div key={key}><dt>{SENIORITY_LABELS[key] ?? key}</dt><dd>{value}</dd></div>)}</dl></section>
        <section><h3>岗位族分布</h3><dl>{Object.entries(dimensions.job_families).map(([key, value]) => <div key={key}><dt>{FAMILY_LABELS[key] ?? key}</dt><dd>{value}</dd></div>)}</dl></section>
        <section><h3>显式技术栈</h3><div className="hr-panorama-skill-list">{dimensions.skills.slice(0, 16).map((item) => <span key={item.name}>{item.name}<b>{item.job_count}</b></span>)}</div></section>
      </div>
      <aside><strong>{dimensions.trend.message}</strong><span>{dimensions.interpretation_limits.join("；")}</span></aside>
    </section>}

    {view === "strategy" && <section className="hr-panorama-strategy" data-report-view="strategy">
      <header><p>PRODUCT &amp; BUSINESS SIGNALS</p><h2>产品与业务方向</h2><span>方向判断来自公开岗位事实；推断与事实严格分开。</span></header>
      <div className="hr-panorama-strategy-grid">
        <section><h3>研发资源方向</h3>{directionEntries.length
          ? <dl>{directionEntries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{clusterValue(value)}</dd></div>)}</dl>
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
        <label><span>招聘类型</span><select aria-label="招聘类型" onChange={(event) => setTrackFilter(event.target.value as RecruitmentTrack | "all")} value={trackFilter}><option value="all">全部类型</option><option value="social">社招</option><option value="campus">校招</option><option value="intern">实习</option><option value="unknown">待分类</option></select></label>
        <label><span>地点</span><select aria-label="地点" onChange={(event) => setLocationFilter(event.target.value)} value={locationFilter}><option value="all">全部地点</option>{locations.map((location) => <option key={location} value={location}>{location}</option>)}</select></label>
        <label><span>岗位状态</span><select aria-label="岗位状态" onChange={(event) => setStatusFilter(event.target.value as HrPanoramaSnapshot["status"] | "all")} value={statusFilter}><option value="all">全部状态</option><option value="open">招聘中</option><option value="closed">已关闭</option><option value="unknown">待确认</option></select></label>
        <label><span>技术方向</span><select aria-label="技术方向" onChange={(event) => setDirectionFilter(event.target.value)} value={directionFilter}><option value="all">全部方向</option>{directions.map((direction) => <option key={direction} value={direction}>{direction}</option>)}</select></label>
      </div>
      <JobCards items={filteredJobs.slice(0, jobLimit)} sourceById={sourceById} />
      {filteredJobs.length > jobLimit && <button className="hr-panorama-load-more" onClick={() => setJobLimit((value) => value + 100)} type="button">再显示 100 条</button>}
    </section>}

    {view === "evidence" && <section className="hr-panorama-source-matrix" data-evidence-kind="source-matrix">
      <header><p>SOURCE MATRIX</p><h2>情报来源矩阵</h2><span>逐家公司展示批准渠道、本版观测结果和失败边界。</span></header>
      <div>{report.sources.map((source) => {
        const snapshots = report.snapshots.filter((item) => item.sourceId === source.sourceId);
        const coverage = coverageById.get(source.sourceId);
        const unavailable = coverage?.state === "failed" || coverage?.state === "not_observed";
        const channels = channelSnapshots(source, snapshots);
        return <article key={source.sourceId}><header><div><h3>{source.canonicalName}</h3><span>{coverage?.state === "failed" ? "来源读取失败，无法判断岗位数量" : coverage?.state === "not_observed" ? "本版未观测，无法判断岗位数量" : `${source.approvedUrls.length} 个来源渠道`}</span></div></header>
          <ul>{[...channels].map(([url, items]) => {
            const observed = items.map((item) => item.observedAt).sort();
            const latest = observed[observed.length - 1];
            const failureCode = coverage?.channelFailures?.[url];
            const checkedEmpty = coverage?.state === "empty_confirmed" && coverage.sourceUrls.includes(url);
            return <li key={url}><span>{sourceChannel(url)}</span><a href={url} rel="noreferrer" target="_blank">{url} ↗</a><small>{failureCode ? `来源失败 · ${collectionFailureLabel(failureCode)}` : items.length ? `命中 ${items.length} 条` : checkedEmpty ? "已核验，本版未发现公开岗位" : unavailable ? "无法判断" : "本版没有岗位证据"}</small>{latest && <time dateTime={latest}>最近观测 {time(latest)}</time>}</li>;
          })}</ul>
        </article>;
      })}</div>
    </section>}

    {view === "evidence" && <section className="hr-panorama-evidence" data-evidence-kind="facts">
      <header><p>FACTS</p><h2>公开事实</h2><span>以下内容可回到原始公开页面核验。</span></header>
      <ol>{report.insight.facts.map((fact) => <li id={`panorama-fact-${encodeURIComponent(fact.factId)}`} key={fact.factId}>
        <p>{fact.text}</p>
        <footer><a href={fact.sourceUrl} rel="noreferrer" target="_blank">查看公开来源 ↗</a><time dateTime={fact.observedAt}>观测于 {time(fact.observedAt)}</time></footer>
      </li>)}</ol>
    </section>}

    {view === "evidence" && <section className="hr-panorama-evidence is-inference" data-evidence-kind="inferences">
      <header><p>INFERENCES</p><h2>AI 推断</h2><span>推断不是公开事实，并列出其事实依据。</span></header>
      {report.insight.inferences.length ? <ol>{report.insight.inferences.map((item, index) => <li key={`${index}:${item.text}`}>
        <p>{item.text}</p><div className="hr-panorama-inference-basis">{item.basisFactIds.map((factId) => factById.get(factId)).filter((fact) => fact !== undefined).map((fact) => <span key={fact.factId}><a href={fact.sourceUrl} rel="noreferrer" target="_blank">{fact.text} ↗</a><time dateTime={fact.observedAt}>观测于 {time(fact.observedAt)}</time></span>)}</div>
      </li>)}</ol> : <p className="hr-panorama-empty-copy">本版没有发布 AI 推断。</p>}
    </section>}

    {view === "evidence" && <section className="hr-panorama-evidence is-unknown" data-evidence-kind="unknowns">
      <header><p>UNKNOWNS</p><h2>仍待确认</h2><span>公开材料没有证明的事项，不作为否定结论。</span></header>
      {report.insight.unknowns.length ? <ul>{report.insight.unknowns.map((item, index) => <li key={`${index}:${item.text}`}>{item.text}</li>)}</ul> : <p className="hr-panorama-empty-copy">本版没有额外未知项。</p>}
    </section>}

    {view === "evidence" && <section className="hr-panorama-sources">
      <header><p>SOURCES</p><h2>来源记录</h2><span>每条岗位记录保留公开链接和实际观测时间。</span></header>
      {report.snapshots.length ? <><p className="hr-panorama-track-warning">页面展示前 200 条岗位来源；完整原始数据请下载 Excel。</p><div>{report.snapshots.slice(0, 200).map((item) => <article key={item.snapshotId}>
        <span>{sourceById.get(item.sourceId)?.canonicalName ?? "关注公司"}</span><h3>{item.title}</h3><p>{item.location} · {item.dutyExcerpt}</p>
        <footer><a href={item.sourceUrl} rel="noreferrer" target="_blank">打开岗位来源 ↗</a><time dateTime={item.observedAt}>{time(item.observedAt)}</time></footer>
      </article>)}</div></> : <p className="hr-panorama-empty-copy">本版没有可展示的岗位快照。</p>}
    </section>}

    {view === "evidence" && <section className="hr-panorama-sources" data-evidence-kind="raw-responses">
      <header><p>RAW SOURCE RESPONSES</p><h2>原始来源响应</h2><span>按内容哈希永久保留，与 AI 分析分开，可下载复核。</span></header>
      {report.evidence.length ? <div>{report.evidence.map((item) => <article key={`${item.sourceId}:${item.sourceUrl}:${item.attemptNumber}`}>
        <span>{sourceById.get(item.sourceId)?.canonicalName ?? "关注公司"} · 官方来源响应</span><h3>{item.mime} · {item.sizeBytes} bytes</h3><p>SHA-256: {item.sha256}</p>
        <footer><a download href={platformPath(`/api/hr/panorama/reports/${encodeURIComponent(report.publication.publicationId)}/evidence/${item.sha256}`)}>下载原始响应</a><time dateTime={item.observedAt}>{time(item.observedAt)}</time></footer>
      </article>)}</div> : <p className="hr-panorama-empty-copy">本版没有可下载的原始响应。</p>}
    </section>}

    <details className="hr-panorama-diagnostics">
      <summary>高级诊断</summary>
      <dl><div><dt>发布版本</dt><dd>{report.publication.publicationId}</dd></div><div><dt>模型版本</dt><dd>{report.insight.modelVersion}</dd></div></dl>
    </details>
  </article>;
}
