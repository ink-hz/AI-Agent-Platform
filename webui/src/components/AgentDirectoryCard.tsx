import { sourceFreshnessLabel } from "../copy";
import { formatCount } from "../fleet";
import type { AgentSummary } from "../types";
import { PlatformLink } from "./PlatformLink";


export function AgentDirectoryCard({ agent }: { agent: AgentSummary }) {
  return (
    <PlatformLink className={`directory-agent agent-${agent.accent}`} href={`/agents/${encodeURIComponent(agent.id)}`}>
      <span className="directory-accent" />
      <div className="directory-agent-head">
        <span className="fleet-avatar">{agent.glyph}</span>
        <div><p>{agent.domain}</p><h2>{agent.name}</h2></div>
        <span className={`freshness freshness-${agent.freshness}`}>{sourceFreshnessLabel(agent.freshness)}</span>
      </div>
      <p className="directory-description">{agent.description}</p>
      <dl className="directory-metrics">
        <div><dt>累计 Session</dt><dd>{formatCount(agent.session_count)}</dd></div>
        <div><dt>累计对话</dt><dd>{formatCount(agent.total_turns)}</dd></div>
      </dl>
      <div className="directory-foot"><span>{agent.deployment}</span><span>{agent.source_kind.toUpperCase()} →</span></div>
    </PlatformLink>
  );
}
