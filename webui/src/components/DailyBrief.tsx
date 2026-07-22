import { briefStatusLabel } from "../operations";
import type { OperationsBrief } from "../types";
import { OperationalEventItem } from "./OperationalEventItem";
import { PlatformLink } from "./PlatformLink";


function usageSummary(brief: OperationsBrief): string {
  const conversationLabel = brief.usage.conversations === 1 ? "conversation" : "conversations";
  const agentLabel = brief.usage.active_agents === 1 ? "Business Agent" : "Business Agents";
  return `${brief.usage.conversations} new ${conversationLabel} across ${brief.usage.active_agents} ${agentLabel}`;
}

export function DailyBrief({ brief, stale = false }: { brief: OperationsBrief; stale?: boolean }) {
  const status = stale ? "stale" : brief.freshness.status;
  const canClaimHealthy = !stale && status === "current" && brief.can_claim_healthy;
  const activeAttention = brief.attention.filter((event) => event.status === "active");
  const changes = brief.changes.slice(0, 5);

  return (
    <section className="daily-brief" aria-labelledby="daily-brief-title">
      <header className="daily-brief-heading">
        <div><p>OPERATIONS</p><h2 id="daily-brief-title">Daily Brief</h2></div>
        <div className={`brief-freshness brief-status-${status}`} role={status === "current" ? undefined : "status"}>
          <span aria-hidden="true" />{briefStatusLabel(brief.freshness, stale)}
        </div>
      </header>
      <div className="daily-brief-grid">
        <article className="brief-panel attention-panel">
          <div className="brief-panel-heading"><span aria-hidden="true">!</span><h3>Needs Attention</h3></div>
          {activeAttention.length > 0
            ? <div className="operational-event-list">{activeAttention.map((event) => <OperationalEventItem event={event} key={event.event_id} />)}</div>
            : canClaimHealthy
              ? <div className="brief-zero-state brief-healthy"><span aria-hidden="true">✓</span><div><strong>No critical issues</strong><p>All required operational checks completed.</p></div></div>
              : <div className="brief-zero-state"><span aria-hidden="true">—</span><div><strong>Attention status unavailable</strong><p>The latest evaluation cannot support a health claim.</p></div></div>}
        </article>
        <article className="brief-panel changes-panel">
          <div className="brief-panel-heading brief-changes-heading">
            <div><span aria-hidden="true">↗</span><h3>Last 24 Hours</h3></div>
            <PlatformLink href="/activity">View all activity →</PlatformLink>
          </div>
          <div className="usage-brief">
            <strong>{usageSummary(brief)}</strong>
            {brief.usage.leaders.length > 0 && <p>Led by {brief.usage.leaders.slice(0, 3).map((leader) => `${leader.agent_name} (${leader.conversations})`).join(", ")}</p>}
          </div>
          {changes.length > 0
            ? <div className="operational-event-list change-list">{changes.map((event) => <OperationalEventItem event={event} key={event.event_id} />)}</div>
            : <p className="brief-no-changes">No notable changes recorded.</p>}
        </article>
      </div>
    </section>
  );
}
