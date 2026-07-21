import { useEffect, useState } from "react";

import { fetchFlywheelItems, fetchFlywheelOverview, fetchSyncStatus } from "../api";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import type { FlywheelOverview, ImprovementItem, Page, SyncStatus } from "../types";


export function FlywheelPage() {
  const [overview, setOverview] = useState<FlywheelOverview | null>(null);
  const [items, setItems] = useState<Page<ImprovementItem> | null>(null);
  const [sync, setSync] = useState<SyncStatus[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([fetchFlywheelOverview(controller.signal), fetchFlywheelItems(controller.signal), fetchSyncStatus(controller.signal)])
      .then(([nextOverview, nextItems, nextSync]) => { setOverview(nextOverview); setItems(nextItems); setSync(nextSync); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);
  if (error) return <ErrorState />;
  if (!overview || !items) return <LoadingState label="Loading Flywheel" />;
  const metrics = [
    ["Feedback", overview.feedback_total], ["Negative", overview.negative_feedback],
    ["Pending Review", overview.pending_reviews], ["Eval Candidates", overview.evaluation_candidates],
    ["Knowledge Tasks", overview.knowledge_tasks], ["QA Candidates", overview.qa_candidates],
  ] as const;
  return <>
    <section className="page-intro"><div><p className="eyebrow">DATA FLYWHEEL</p><h1>Flywheel</h1><p>Read-only view of captured Feedback, human Review, and improvement candidates across MetaBot, FAE, and Admin.</p></div></section>
    <section className="flywheel-metrics">{metrics.map(([label, value]) => <article key={label}><span>{label}</span><strong>{value.toLocaleString("en-US")}</strong></article>)}</section>
    <section className="sync-strip"><div><p>REMOTE MIRROR</p><h2>Daily sync</h2></div>{sync.map((source) => <article key={source.source_kind}><span>{source.source_kind.toUpperCase()}</span><strong className={`sync-${source.status}`}>{source.status}</strong><time>{source.completed_at ? new Date(source.completed_at).toLocaleString() : "In progress"}</time></article>)}</section>
    <section className="detail-section"><div className="section-heading"><div><p>IMPROVEMENT QUEUE</p><h2>Candidates and tasks</h2></div><span>{items.total} items</span></div>
      {items.items.length ? <div className="improvement-list">{items.items.map((item) => <article key={item.item_key}><div><span>{item.item_type}</span><b>{item.status}</b>{item.priority && <b>{item.priority}</b>}</div><h3>{item.title || "Untitled improvement"}</h3><p>{item.summary || "No summary provided."}</p><footer><PlatformLink href={`/agents/${encodeURIComponent(item.agent_id)}`}>{item.agent_id}</PlatformLink><time>{new Date(item.updated_at).toLocaleString()}</time></footer></article>)}</div>
        : <EmptyState title="No improvement items yet" description="Feedback is being captured; Review can promote it into evaluation, knowledge, or QA work." />}
    </section>
  </>;
}
