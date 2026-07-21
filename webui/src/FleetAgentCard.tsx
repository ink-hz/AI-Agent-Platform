import {
  FLEET_STATE_META,
  formatCount,
  formatRelativeActivity,
} from "./fleet";
import { formatUptime } from "./status";
import type { FleetAgent } from "./types";


interface FleetAgentCardProps {
  agent: FleetAgent;
  now: Date;
}


export function FleetAgentCard({ agent, now }: FleetAgentCardProps) {
  const state = FLEET_STATE_META[agent.state];
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
          <span>累计对话</span>
          <strong>{formatCount(agent.total_conversations)}</strong>
        </div>
        <div className="weekly-usage">
          <span>近 7 天</span>
          <b>{formatCount(agent.conversations_last_7d)}</b>
        </div>
      </div>

      <dl className="fleet-agent-meta">
        <div><dt>运行时长</dt><dd>{formatUptime(agent.uptime_seconds)}</dd></div>
        <div>
          <dt>最近活动</dt>
          <dd>{formatRelativeActivity(agent.last_activity_at, now)}</dd>
        </div>
      </dl>

      <div className="fleet-recent">
        <span>最近</span>
        <p>{agent.recent_summary ?? "尚无真实对话记录"}</p>
      </div>
    </article>
  );
}
