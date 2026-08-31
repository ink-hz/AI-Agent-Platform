import { useEffect, useMemo, useState } from "react";

import type { Account } from "../auth";
import { localPathname } from "../auth";
import { LoadingState } from "../components/DataState";
import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { ReviewWorkspace } from "../components/review/ReviewWorkspace";
import { FaeWorkbenchApiError, faeWorkbenchApi } from "../faeWorkbenchApi";
import { navigate } from "../router";
import type { ReviewInboxItem } from "../types";
import { STATUS_LABELS } from "../components/review/IssueList";


function pathIssueId(): string | null {
  const match = /^\/admin\/fae\/issues\/([0-9a-fA-F-]{36})$/.exec(localPathname());
  return match?.[1] ?? null;
}

const FAE_ISSUE_STATUSES = new Set(["open", "actionable", ...Object.keys(STATUS_LABELS)]);

function issueStatusFromSearch(search = window.location.search): string {
  const values = new URLSearchParams(search).getAll("status");
  return values.length === 1 && FAE_ISSUE_STATUSES.has(values[0]) ? values[0] : "";
}

export function FaeIssuesPage({ account, issueId }: { account: Account; issueId?: string }) {
  const query = new URLSearchParams(window.location.search);
  const sessionKey = query.get("session_key");
  const turnKey = query.get("turn_key");
  const hasTurnDeepLink = Boolean(sessionKey && turnKey);
  const [initialTurn, setInitialTurn] = useState<ReviewInboxItem | null>(null);
  const [loadingTurn, setLoadingTurn] = useState(hasTurnDeepLink);
  const [turnError, setTurnError] = useState<"missing" | "forbidden" | "unavailable" | null>(null);
  const [issueStatus, setIssueStatus] = useState(issueStatusFromSearch);
  const reviewApi = useMemo(() => faeWorkbenchApi.review(account.csrf_token), [account.csrf_token]);
  const issueFilters = useMemo(() => issueStatus === "actionable"
    ? { disposition: "actionable" }
    : issueStatus ? { status: issueStatus } : undefined, [issueStatus]);

  useEffect(() => {
    const restore = () => setIssueStatus(issueStatusFromSearch());
    restore();
    window.addEventListener("popstate", restore);
    window.addEventListener("platform:navigate", restore);
    return () => {
      window.removeEventListener("popstate", restore);
      window.removeEventListener("platform:navigate", restore);
    };
  }, []);

  const changeIssueStatus = (status: string) => {
    const query = new URLSearchParams(window.location.search);
    if (status) query.set("status", status);
    else query.delete("status");
    const search = query.toString();
    navigate(`${localPathname()}${search ? `?${search}` : ""}`);
  };

  useEffect(() => {
    const controller = new AbortController();
    setInitialTurn(null);
    setTurnError(null);
    if (!sessionKey || !turnKey) {
      setLoadingTurn(false);
      return () => controller.abort();
    }
    setLoadingTurn(true);
    void faeWorkbenchApi.session(sessionKey, controller.signal).then((session) => {
      if (controller.signal.aborted) return;
      const turn = session.turns.find((item) => item.turn_key === turnKey);
      if (!turn) {
        setTurnError("missing");
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
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      if (error instanceof FaeWorkbenchApiError && error.status === 404) setTurnError("missing");
      else if (error instanceof FaeWorkbenchApiError && (error.status === 401 || error.status === 403)) setTurnError("forbidden");
      else setTurnError("unavailable");
    }).finally(() => {
      if (!controller.signal.aborted) setLoadingTurn(false);
    });
    return () => controller.abort();
  }, [sessionKey, turnKey]);

  let content;
  if (loadingTurn) content = <LoadingState label="正在加载原始回答" />;
  else if (turnError) content = <section className="permission-state" role="alert">{turnError === "missing"
    ? <><h1>找不到原始回答</h1><p>该 Session 中不存在指定 Turn，无法创建治理事项。</p></>
    : turnError === "forbidden"
      ? <><h1>无权读取原始回答</h1><p>当前账号无法读取该 FAE Session，无法创建治理事项。</p></>
      : <><h1>原始回答暂不可用</h1><p>读取 FAE Session 时遇到服务或数据异常，请稍后重试。</p></>}
  </section>;
  else content = <ReviewWorkspace
    api={reviewApi}
    agentId="ai-fae-agent"
    basePath="/admin/fae/issues"
    initialIssueId={issueId ?? pathIssueId()}
    initialTurn={initialTurn}
    actor={`corp:${account.internal_user_id}`}
    showActorField={false}
    showAgentFilter={false}
    statusFilter={issueStatus}
    onStatusFilterChange={changeIssueStatus}
    issueFilters={issueFilters}
    readOnlyReason={account.hard_stale_read_only ? "hard-stale" : undefined}
  />;

  return <FaeWorkbenchShell currentSection="issues">{content}</FaeWorkbenchShell>;
}
