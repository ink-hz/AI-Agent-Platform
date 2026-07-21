import type { AgentSummary } from "../types";


export function AgentDataSwitcher({
  agents,
  selectedId,
  onSelect,
}: {
  agents: AgentSummary[];
  selectedId: string;
  onSelect: (agentId: string) => void;
}) {
  return <div className="agent-data-switcher" aria-label="Select Agent" role="group">
    {agents.map((agent) => {
      const selected = agent.id === selectedId;
      return <button
        aria-pressed={selected}
        className={`agent-data-option agent-${agent.accent}${selected ? " is-selected" : ""}`}
        data-agent-id={agent.id}
        key={agent.id}
        onClick={() => onSelect(agent.id)}
        type="button"
      >
        <span className="fleet-avatar" aria-hidden="true">{agent.glyph}</span>
        <span className="agent-data-option-copy"><strong>{agent.name}</strong><small>{agent.source_kind.toUpperCase()}</small></span>
      </button>;
    })}
  </div>;
}
