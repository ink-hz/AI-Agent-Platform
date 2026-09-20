import { useEffect, useMemo, useRef, useState } from "react";

import { platformPath } from "../auth";
import type { AiEngineeringDocumentSlug } from "../aiEngineeringApi";
import type { PanoramaActionId, PanoramaData, PanoramaDomain } from "../panoramaTypes";
import "./panorama.css";


const ACTION_LABELS: Record<PanoramaActionId, string> = {
  brain: "使用大脑", agents: "Agent 目录", missions: "任务", sessions: "会话", operations: "运行总览",
  review: "复审", activity: "运行记录", identity: "身份", governance: "治理", access: "访问记录",
  account: "账号", "agent-admin": "Agent 管理", notes: "建设笔记", hr: "HR · 独立入口",
  office: "行政 · 独立入口", voc: "VOC · 独立入口", fae: "FAE · 独立入口",
};
const EXTERNAL_ACTIONS = new Set<PanoramaActionId>(["hr", "office", "voc", "fae"]);
const REVENUE_COLORS = ["panorama-revenue__segment--one", "panorama-revenue__segment--two", "panorama-revenue__segment--three", "panorama-revenue__segment--four"];

interface Props {
  data: PanoramaData;
  onAction: (actionId: PanoramaActionId) => void;
  onEvidence: (slug: AiEngineeringDocumentSlug) => void;
  isOwner?: boolean;
}

function percent(amount: number, total: number): string { return `${(amount / total * 100).toFixed(2)}%`; }

function Sources({ ids, data, onEvidence }: { ids: string[]; data: PanoramaData; onEvidence: Props["onEvidence"] }) {
  return <div className="panorama-sources" aria-label="事实来源">{ids.map((id) => {
    const source = data.sources.find((item) => item.id === id);
    return source && <button type="button" className="panorama-source" data-source-id={id} key={id} onClick={() => onEvidence(source.document)}>{source.label}</button>;
  })}</div>;
}

function Action({ id, onAction }: { id: PanoramaActionId; onAction: Props["onAction"] }) {
  return <button type="button" className={`panorama-action${EXTERNAL_ACTIONS.has(id) ? " panorama-action--external" : ""}`} data-action-id={id} onClick={() => onAction(id)}>
    {ACTION_LABELS[id]}{EXTERNAL_ACTIONS.has(id) && <span aria-hidden="true"> ↗</span>}
  </button>;
}

function DomainCard({ domain, data, expanded, related, match, onToggle, onAction, onEvidence }: {
  domain: PanoramaDomain; data: PanoramaData; expanded: boolean; related: boolean; match: boolean;
  onToggle: () => void; onAction: Props["onAction"]; onEvidence: Props["onEvidence"];
}) {
  return <article className={`panorama-domain${related ? " is-related" : ""}${match ? " is-search-match" : ""}`} data-domain-id={domain.id} data-expanded={expanded}>
    <button type="button" className="panorama-domain__toggle" aria-expanded={expanded} onClick={onToggle}>
      <span className="panorama-domain__heading"><strong>{domain.title}</strong><small>{domain.subtitle}</small></span>
      <span className="panorama-status">{domain.status}</span><span className="panorama-chevron" aria-hidden="true">{expanded ? "−" : "+"}</span>
    </button>
    <ul className="panorama-domain__items">{domain.items.map((item) => <li key={item}>{item}</li>)}</ul>
    {expanded && <div className="panorama-domain__detail">
      <ul>{domain.detail.map((item) => <li key={item}>{item}</li>)}</ul>
      <div className="panorama-actions">{domain.actions.map((id) => <Action id={id} key={id} onAction={onAction} />)}</div>
      <Sources ids={domain.source_ids} data={data} onEvidence={onEvidence} />
    </div>}
  </article>;
}

export function PanoramaView({ data, onAction, onEvidence, isOwner = false }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [presenting, setPresenting] = useState(false);
  const queryRef = useRef<HTMLInputElement>(null);
  const domains = [...data.domains, ...data.support];
  const matchIds = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return new Set<string>();
    return new Set(domains.filter((domain) => [domain.title, domain.subtitle, domain.status, ...domain.items, ...domain.detail].join(" ").toLocaleLowerCase().includes(needle)).map(({ id }) => id));
  }, [data, query]);
  useEffect(() => { if (query.trim() && matchIds.size) setExpandedId([...matchIds][0]); }, [query, matchIds]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setExpandedId(null); setQuery(""); setPresenting(false); }
      if (event.key === "/" && document.activeElement !== queryRef.current) { event.preventDefault(); queryRef.current?.focus(); }
    };
    document.addEventListener("keydown", onKeyDown); return () => document.removeEventListener("keydown", onKeyDown);
  }, []);
  const selected = domains.find(({ id }) => id === expandedId);
  const relatedIds = new Set(selected?.related_ids ?? []);
  const sharedActions = data.shared.actions.filter((id) => id !== "access" || isOwner);
  let x = 0;

  return <main className={`panorama${presenting ? " panorama--presenting" : ""}`} aria-label={data.title}>
    <header className="panorama-header">
      <div><p className="panorama-eyebrow">AI ENGINEERING PANORAMA · {data.version}</p><h1>{data.title}</h1><p>{data.context.observation}</p></div>
      <div className="panorama-toolbar">
        <label className="panorama-search"><span>搜索全景</span><input ref={queryRef} type="search" value={query} placeholder="产品、能力或状态…" onInput={(event) => setQuery(event.currentTarget.value)} /></label>
        <button type="button" onClick={() => setPresenting((value) => !value)}>{presenting ? "退出展示" : "展示模式"}</button>
        <a className="panorama-export" href={platformPath("/api/v1/ai-engineering/export.svg")} download>导出 SVG</a>
        <a className="panorama-export" href={platformPath("/api/v1/ai-engineering/export.png")} download>导出 PNG</a>
      </div>
    </header>

    <section className="panorama-topline" aria-label="经营背景与收入构成">
      <article className="panorama-context">
        <div className="panorama-section-title"><span>01</span><div><h2>为什么现在做 AI</h2><p>{data.context.period}</p></div></div>
        <div className="panorama-metrics">{data.context.metrics.map((metric) => <div key={metric.label}><strong>{metric.value}</strong><span>{metric.label}</span></div>)}</div>
        <p className="panorama-judgment">{data.context.judgment}</p><Sources ids={data.context.source_ids} data={data} onEvidence={onEvidence} />
      </article>
      <article className="panorama-revenue">
        <div className="panorama-section-title"><span>02</span><div><h2>收入构成</h2><p>{data.revenue.period} · 同尺度</p></div></div>
        <svg className="panorama-revenue__bar" viewBox="0 0 1000 38" role="img" aria-label="各收入分类按原金额计算的占比">
          {data.revenue.segments.map((segment, index) => { const width = segment.amount_cents / data.revenue.denominator_cents * 1000; const start = x; x += width; return <rect data-revenue-segment={segment.id} className={REVENUE_COLORS[index % REVENUE_COLORS.length]} key={segment.id} x={start} y="0" width={width} height="38" />; })}
        </svg>
        <div className="panorama-revenue__legend">{data.revenue.segments.map((segment, index) => <div key={segment.id}><i className={REVENUE_COLORS[index % REVENUE_COLORS.length]} /><span>{segment.label}</span><strong>{percent(segment.amount_cents, data.revenue.denominator_cents)}</strong></div>)}</div>
        <p className="panorama-note">{data.revenue.note}</p><Sources ids={data.revenue.source_ids} data={data} onEvidence={onEvidence} />
      </article>
    </section>

    <section className="panorama-value-chain" aria-label="业务价值链与 AI 覆盖">
      <div className="panorama-section-title"><span>03</span><div><h2>业务价值链与 AI 覆盖</h2><p>点击领域查看细节；关联领域同步高亮</p></div></div>
      <div className="panorama-domain-grid">{data.domains.map((domain, index) => <div className="panorama-domain-slot" key={domain.id}>
        <DomainCard domain={domain} data={data} expanded={expandedId === domain.id} related={relatedIds.has(domain.id)} match={matchIds.has(domain.id)} onToggle={() => setExpandedId((id) => id === domain.id ? null : domain.id)} onAction={onAction} onEvidence={onEvidence} />
        {index < data.domains.length - 1 && <span className="panorama-flow" aria-hidden="true">→</span>}
      </div>)}</div>
      {query.trim() && !matchIds.size && <p className="panorama-empty" role="status">未在当前全景中找到“{query}”</p>}
    </section>

    <section className="panorama-foundation" aria-label="管理与共用能力">
      <div className="panorama-support">{data.support.map((domain) => <DomainCard key={domain.id} domain={domain} data={data} expanded={expandedId === domain.id} related={relatedIds.has(domain.id)} match={matchIds.has(domain.id)} onToggle={() => setExpandedId((id) => id === domain.id ? null : domain.id)} onAction={onAction} onEvidence={onEvidence} />)}</div>
      <article className="panorama-shared"><div><p>PLATFORM LAYER</p><h2>{data.shared.title}</h2><span>{data.shared.status}</span></div><div className="panorama-actions">{sharedActions.map((id) => <Action id={id} key={id} onAction={onAction} />)}</div></article>
    </section>

    <section className="panorama-asks" aria-label="请管理层协调"><div className="panorama-section-title"><span>04</span><div><h2>请管理层协调</h2><p>提议 · 尚未获批</p></div></div><ol>{data.asks.map((ask) => <li key={`${ask.owner}:${ask.request}`}><strong>{ask.owner}</strong><span>{ask.request}</span></li>)}</ol></section>
    <footer className="panorama-footer"><span>数据时间 {data.updated_at}</span><span>版本 {data.version}</span></footer>
  </main>;
}
