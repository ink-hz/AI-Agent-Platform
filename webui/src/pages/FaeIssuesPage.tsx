import { useEffect, useState } from "react";

import type { Account } from "../auth";
import { localPathname } from "../auth";
import { LoadingState } from "../components/DataState";
import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { ReviewWorkspace } from "../components/review/ReviewWorkspace";
import { faeWorkbenchApi } from "../faeWorkbenchApi";
import type { ReviewInboxItem } from "../types";


function pathIssueId(): string | null {
  const match = /^\/admin\/fae\/issues\/([0-9a-fA-F-]{36})$/.exec(localPathname());
  return match?.[1] ?? null;
}

export function FaeIssuesPage({ account, issueId }: { account: Account; issueId?: string }) {
  const query = new URLSearchParams(window.location.search);
  const sessionKey = query.get("session_key");
  const turnKey = query.get("turn_key");
  const hasTurnDeepLink = Boolean(sessionKey && turnKey);
  const [initialTurn, setInitialTurn] = useState<ReviewInboxItem | null>(null);
  const [loadingTurn, setLoadingTurn] = useState(hasTurnDeepLink);
  const [turnMissing, setTurnMissing] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setInitialTurn(null);
    setTurnMissing(false);
    if (!sessionKey || !turnKey) {
      setLoadingTurn(false);
      return () => controller.abort();
    }
    setLoadingTurn(true);
    void faeWorkbenchApi.session(sessionKey, controller.signal).then((session) => {
      if (controller.signal.aborted) return;
      const turn = session.turns.find((item) => item.turn_key === turnKey);
      if (!turn) {
        setTurnMissing(true);
        return;
      }
      setInitialTurn({
        agent_id: "ai-fae-agent",
        turn_key: turn.turn_key,
        question: turn.question,
        answer: turn.answer,
        feedback_keys: turn.feedback.map((item) => item.feedback_key),
        first_feedback_at: turn.feedback[0]?.created_at ?? turn.created_at,
      });
    }).catch(() => {
      if (!controller.signal.aborted) setTurnMissing(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoadingTurn(false);
    });
    return () => controller.abort();
  }, [sessionKey, turnKey]);

  let content;
  if (loadingTurn) content = <LoadingState label="正在加载原始回答" />;
  else if (turnMissing) content = <section className="permission-state" role="alert"><h1>找不到原始回答</h1><p>该 Session 中不存在指定 Turn，无法创建治理事项。</p></section>;
  else content = <ReviewWorkspace
    api={faeWorkbenchApi.review}
    agentId="ai-fae-agent"
    basePath="/admin/fae/issues"
    initialIssueId={issueId ?? pathIssueId()}
    initialTurn={initialTurn}
    actor={`corp:${account.internal_user_id}`}
    showActorField={false}
    showAgentFilter={false}
  />;

  return <FaeWorkbenchShell currentSection="issues">{content}</FaeWorkbenchShell>;
}
