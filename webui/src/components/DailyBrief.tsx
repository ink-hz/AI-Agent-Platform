import { briefStatusLabel } from "../operations";
import type { OperationsBrief } from "../types";
import { OperationalEventItem } from "./OperationalEventItem";
import { PlatformLink } from "./PlatformLink";


function usageSummary(brief: OperationsBrief): string {
  return `${brief.usage.active_agents} 个业务 Agent 新增 ${brief.usage.conversations} 次对话`;
}

export function DailyBrief({ brief, stale = false }: { brief: OperationsBrief; stale?: boolean }) {
  const status = stale ? "stale" : brief.freshness.status;
  const canClaimHealthy = !stale && status === "current" && brief.can_claim_healthy;
  const activeAttention = brief.attention.filter((event) => event.status === "active");
  const changes = brief.changes.slice(0, 5);

  return (
    <section className="daily-brief" aria-labelledby="daily-brief-title">
      <header className="daily-brief-heading">
        <div><h2 id="daily-brief-title">今日运行摘要</h2></div>
        <div className={`brief-freshness brief-freshness-${status}`} role={status === "current" ? undefined : "status"}>
          <span aria-hidden="true" />{briefStatusLabel(brief.freshness, stale)}
        </div>
      </header>
      <div className="daily-brief-grid">
        <article className="brief-panel attention-panel">
          <div className="brief-panel-heading"><span aria-hidden="true">!</span><h3>需要关注</h3></div>
          {activeAttention.length > 0
            ? <div className="operational-event-list">{activeAttention.map((event) => <OperationalEventItem event={event} key={event.event_id} />)}</div>
            : canClaimHealthy
              ? <div className="brief-zero-state brief-healthy"><span aria-hidden="true">✓</span><div><strong>暂无严重问题</strong><p>当前关键运行检查均正常。</p></div></div>
              : <div className="brief-zero-state"><span aria-hidden="true">—</span><div><strong>关注状态暂不可用</strong><p>当前观测信息不足，无法确认整体正常。</p></div></div>}
        </article>
        <article className="brief-panel changes-panel">
          <div className="brief-panel-heading brief-changes-heading">
            <div><span aria-hidden="true">↗</span><h3>近 24 小时</h3></div>
            <PlatformLink href="/activity">查看全部运行记录 →</PlatformLink>
          </div>
          <div className="usage-brief">
            <strong>{usageSummary(brief)}</strong>
            {brief.usage.leaders.length > 0 && <p>活跃 Agent：{brief.usage.leaders.slice(0, 3).map((leader) => `${leader.agent_name} (${leader.conversations})`).join("、")}</p>}
          </div>
          {changes.length > 0
            ? <div className="operational-event-list change-list">{changes.map((event) => <OperationalEventItem event={event} key={event.event_id} />)}</div>
            : <p className="brief-no-changes">近 24 小时暂无重要变化。</p>}
        </article>
      </div>
    </section>
  );
}
