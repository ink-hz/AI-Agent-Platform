import type { ReactNode } from "react";

import { sourceFreshnessLabel } from "../../copy";
import type { SessionDetail, TurnClosureSummary, TurnDetail } from "../../types";
import { PlatformLink } from "../PlatformLink";
import { TurnCard } from "../TurnCard";


export interface SessionReplayProps {
  session: SessionDetail;
  closureSummaries: Record<string, TurnClosureSummary>;
  governanceHref?: (turn: TurnDetail) => string | null;
  betweenHeaderAndTurns?: ReactNode;
}


export function SessionReplay({ session, closureSummaries, governanceHref, betweenHeaderAndTurns }: SessionReplayProps) {
  return <>
    <section className="session-detail-head">
      <div><p>Session 回放</p><h1>{session.title || "未命名 Session"}</h1><code>{session.session_key}</code></div>
      <div className="session-detail-source"><span>{session.source_kind.toUpperCase()}</span><strong>{session.channel}</strong><small className={`freshness freshness-${session.freshness}`}>{sourceFreshnessLabel(session.freshness)}</small></div>
      <dl><div><dt>Agent</dt><dd><PlatformLink href={`/admin/agents/${encodeURIComponent(session.agent_id)}`}>{session.agent_id}</PlatformLink></dd></div><div><dt>对话轮次</dt><dd>{session.turn_count}</dd></div><div><dt>反馈</dt><dd>{session.feedback_count ?? "暂不可用"}</dd></div><div><dt>复审</dt><dd>{session.review_count ?? "暂不可用"}</dd></div></dl>
    </section>
    {betweenHeaderAndTurns}
    <section className="turn-stack">{session.turns.map((turn) => <TurnCard
      turn={turn}
      closureSummary={closureSummaries[turn.turn_key]}
      governanceHref={governanceHref?.(turn) ?? undefined}
      key={turn.turn_key}
    />)}</section>
  </>;
}
