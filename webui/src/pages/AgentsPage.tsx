import { useEffect, useState } from "react";

import { fetchAgents } from "../api";
import { AgentDirectoryCard } from "../components/AgentDirectoryCard";
import { EmptyState, ErrorState, LoadingState } from "../components/DataState";
import type { AgentSummary } from "../types";


export function AgentsPage() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    fetchAgents(controller.signal).then(setAgents).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [version]);

  return <>
    <section className="page-intro"><div><p className="eyebrow">FLEET DIRECTORY</p><h1>Agents</h1><p>Every Agent in the fleet, with its role, data source, and real conversation history.</p></div>{agents && <strong>{agents.length}<span> Agents</span></strong>}</section>
    {error ? <ErrorState onRetry={() => setVersion((value) => value + 1)} />
      : agents === null ? <LoadingState label="Loading Agent directory" />
      : agents.length === 0 ? <EmptyState title="No Agents found" description="The Agent catalog is currently empty." />
      : <section className="directory-grid">{agents.map((agent) => <AgentDirectoryCard agent={agent} key={agent.id} />)}</section>}
  </>;
}
