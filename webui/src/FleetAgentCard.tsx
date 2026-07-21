import {
  FLEET_STATE_META,
  formatCount,
  formatDaysInProduction,
  formatExactLifecycleTime,
  formatLastUpdated,
  formatLifecycleDate,
} from "./fleet";
import { UI_COPY } from "./copy";
import type { FleetAgent } from "./types";


interface FleetAgentCardProps {
  agent: FleetAgent;
  now: Date;
}


export function FleetAgentCard({ agent, now }: FleetAgentCardProps) {
  const state = FLEET_STATE_META[agent.state];
  const [total, weekly, inProduction, lastUpdated, recent] = UI_COPY.agent.fields;
  const liveSinceDate = formatLifecycleDate(agent.live_since);
  const lastUpdatedDate = formatLifecycleDate(agent.last_updated_at);
  return (
    <article className={`fleet-agent-card agent-${agent.accent} ${state.tone}`}>
      <div className="fleet-agent-head">
        <span className="fleet-avatar" aria-hidden="true">{agent.glyph}</span>
        <div className="fleet-agent-identity">
          <p>{agent.domain}</p>
          <h3>{agent.name}</h3>
        </div>
        <span className={`fleet-state ${state.tone}`}>
          <i aria-hidden="true" />{state.label}
        </span>
      </div>

      <p className="fleet-agent-description">{agent.description}</p>

      <div className="fleet-usage">
        <div>
          <span>{total}</span>
          <strong>{formatCount(agent.total_conversations)}</strong>
        </div>
        <div className="weekly-usage">
          <span>{weekly}</span>
          <b>{formatCount(agent.conversations_last_7d)}</b>
        </div>
      </div>

      <dl className="fleet-agent-meta">
        <div>
          <dt>{inProduction}</dt>
          <dd title={formatExactLifecycleTime(agent.live_since)}>
            <strong>{formatDaysInProduction(agent.live_since, now)}</strong>
            {liveSinceDate !== "Not recorded" && <small>Since {liveSinceDate}</small>}
          </dd>
        </div>
        <div>
          <dt>{lastUpdated}</dt>
          <dd title={formatExactLifecycleTime(agent.last_updated_at)}>
            <strong>{formatLastUpdated(agent.last_updated_at, now)}</strong>
            {lastUpdatedDate !== "Not recorded" && <small>{lastUpdatedDate}</small>}
          </dd>
        </div>
      </dl>

      <div className="fleet-recent">
        <span>{recent}</span>
        <p>{agent.recent_summary ?? UI_COPY.agent.emptyRecent}</p>
      </div>
    </article>
  );
}
