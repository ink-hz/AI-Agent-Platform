import type { AgentSummary } from "../types";
import { AgentDirectoryCard } from "./AgentDirectoryCard";


interface AgentDirectorySectionsProps {
  business: AgentSummary[];
  system: AgentSummary[];
}


export function AgentDirectorySections({ business, system }: AgentDirectorySectionsProps) {
  return <>
    <section className="directory-grid" aria-label="业务 Agent">
      {business.map((agent) => <AgentDirectoryCard agent={agent} key={agent.id} />)}
    </section>
    {system.length > 0 && <section className="system-agent-section" aria-label="系统 Agent">
      <div className="section-heading">
        <div><h2>系统 Agent</h2></div>
        <span>{system.length} 个监控对象</span>
      </div>
      <div className="directory-grid system-agent-grid">
        {system.map((agent) => <AgentDirectoryCard agent={agent} key={agent.id} />)}
      </div>
    </section>}
  </>;
}
