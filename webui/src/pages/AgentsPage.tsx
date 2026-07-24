import { useEffect, useState } from "react";

import { partitionAgents } from "../agentVisibility";
import { fetchAgents } from "../api";
import { AgentDirectorySections } from "../components/AgentDirectorySections";
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
  const { business, system } = partitionAgents(agents ?? []);

  return <>
    <section className="page-intro"><div><h1>Agent 列表</h1><p>查看所有已接入 Agent 的职责、数据来源和真实运行记录。</p></div>{agents && <strong>{business.length}<span> 个业务 Agent</span></strong>}</section>
    {error ? <ErrorState onRetry={() => setVersion((value) => value + 1)} />
      : agents === null ? <LoadingState label="正在加载 Agent 列表" />
      : agents.length === 0 ? <EmptyState title="暂无 Agent" description="当前还没有已接入的 Agent。" />
      : <AgentDirectorySections business={business} system={system} />}
  </>;
}
