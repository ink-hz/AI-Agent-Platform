import type { AgentSummary } from "../types";
import { AgentDirectoryCard } from "./AgentDirectoryCard";


interface AgentDirectorySectionsProps {
  business: AgentSummary[];
  system: AgentSummary[];
}


export function AgentDirectorySections({ business, system }: AgentDirectorySectionsProps) {
  return <>
    <section className="directory-grid" aria-label="Business Agents">
      {business.map((agent) => <AgentDirectoryCard agent={agent} key={agent.id} />)}
    </section>
    {system.length > 0 && <section className="system-agent-section" aria-label="System Agents">
      <div className="section-heading">
        <div><p>INFRASTRUCTURE</p><h2>System Agents</h2></div>
        <span>{system.length} monitored identities</span>
      </div>
      <div className="directory-grid system-agent-grid">
        {system.map((agent) => <AgentDirectoryCard agent={agent} key={agent.id} />)}
      </div>
    </section>}
  </>;
}
