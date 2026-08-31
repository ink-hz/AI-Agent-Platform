import { useEffect, useMemo, useState } from "react";

import type { Account } from "../auth";
import { localPathname } from "../auth";
import { LoadingState } from "../components/DataState";
import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { ReviewWorkspace, type ReviewIssueFilters } from "../components/review/ReviewWorkspace";
import { useDeploymentContext } from "../deploymentContext";
import { FaeWorkbenchApiError, faeWorkbenchApi } from "../faeWorkbenchApi";
import { navigate } from "../router";
import type { ReviewInboxItem } from "../types";
import { STATUS_LABELS } from "../components/review/IssueList";


function pathIssueId(): string | null {
  const match = /^\/admin\/fae\/issues\/([0-9a-fA-F-]{36})$/.exec(localPathname());
  return match?.[1] ?? null;
}

const LOCAL_LIFECYCLE_STATUSES = Object.keys(STATUS_LABELS).filter((status) => status !== "unknown");
const LOCAL_FILTERS = new Set(["open", ...LOCAL_LIFECYCLE_STATUSES]);
const CLOUD_DISPOSITIONS = ["actionable", "duplicate", "not_actionable", "wont_fix"] as const;
const CLOUD_FILTERS = new Set<string>(CLOUD_DISPOSITIONS);
const PRIORITY_FILTERS = new Set(["P0", "P1", "P2", "P3"]);
const LOCAL_STATUS_OPTIONS = [
  { value: "open", label: "开放事项" },
  ...LOCAL_LIFECYCLE_STATUSES.map((value) => ({ value, label: STATUS_LABELS[value as keyof typeof STATUS_LABELS] })),
];
const CLOUD_STATUS_OPTIONS = [
  { value: "open", label: "开放事项" },
  { value: "actionable", label: "需处理" },
  { value: "duplicate", label: "重复事项" },
  { value: "not_actionable", label: "无需处理" },
  { value: "wont_fix", label: "暂不修复" },
];

function issueFilterFromSearch(search: string, cloudReplica: boolean): { value: string; valid: boolean } {
  const query = new URLSearchParams(search);
  const statuses = query.getAll("status");
  const dispositions = query.getAll("disposition");
  if (statuses.length === 0 && dispositions.length === 0) return { value: "", valid: true };
  if (cloudReplica) {
    if (statuses.length === 1 && statuses[0] === "open" && dispositions.length === 0) {
      return { value: "open", valid: true };
    }
    if (statuses.length === 0 && dispositions.length === 1 && CLOUD_FILTERS.has(dispositions[0])) {
      return { value: dispositions[0], valid: true };
    }
  } else if (statuses.length === 1 && dispositions.length === 0 && LOCAL_FILTERS.has(statuses[0])) {
    return { value: statuses[0], valid: true };
  }
  return { value: "", valid: false };
}

function singleSafeValue(
  query: URLSearchParams,
  key: string,
  valid: (value: string) => boolean = (value) => value.length > 0,
): string | null {
  const values = query.getAll(key);
  return values.length === 1 && valid(values[0]) ? values[0] : null;
}

function safeIssueCollectionParams(search: string, cloudReplica: boolean): URLSearchParams {
  const raw = new URLSearchParams(search);
  const safe = new URLSearchParams();
  const status = issueFilterFromSearch(search, cloudReplica);
  if (status.valid && status.value) {
    safe.set(cloudReplica && status.value !== "open" ? "disposition" : "status", status.value);
  }
  const priority = singleSafeValue(raw, "priority", (value) => PRIORITY_FILTERS.has(value));
  const failureLayer = singleSafeValue(
    raw,
    "failure_layer",
    (value) => /^[a-z][a-z0-9_]{0,63}$/.test(value),
  );
  const owner = singleSafeValue(raw, "owner", (value) => value.length <= 160 && value.trim() === value && value.length > 0);
  const text = singleSafeValue(raw, "q", (value) => value.length <= 240 && value.trim() === value && value.length > 0);
  const createdAfter = singleSafeValue(raw, "created_after", (value) => /^\d{4}-\d{2}-\d{2}T00:00:00\+08:00$/.test(value));
  if (priority) safe.set("priority", priority);
  if (failureLayer) safe.set("failure_layer", failureLayer);
  if (owner) safe.set("owner", owner);
  if (text) safe.set("q", text);
  if (createdAfter) safe.set("created_after", createdAfter);
  const page = singleSafeValue(raw, "page", (value) => /^\d+$/.test(value));
  if (page) {
    const parsed = Number(page);
    if (Number.isSafeInteger(parsed) && parsed > 1) safe.set("page", String(parsed));
  }
  return safe;
}

export function FaeIssuesPage({ account, issueId }: { account: Account; issueId?: string }) {
  const { deployment, resolved: deploymentResolved } = useDeploymentContext();
  const cloudReplica = deployment?.mode === "cloud-replica" && deployment.read_only;
  const query = new URLSearchParams(window.location.search);
  const sessionKey = query.get("session_key");
  const turnKey = query.get("turn_key");
  const hasTurnDeepLink = Boolean(sessionKey && turnKey);
  const [initialTurn, setInitialTurn] = useState<ReviewInboxItem | null>(null);
  const [loadingTurn, setLoadingTurn] = useState(hasTurnDeepLink);
  const [turnError, setTurnError] = useState<"missing" | "forbidden" | "unavailable" | null>(null);
  const [filterRevision, setFilterRevision] = useState(0);
  const parsedIssueFilter = issueFilterFromSearch(window.location.search, cloudReplica);
  const issueStatus = parsedIssueFilter.value;
  const collectionParams = safeIssueCollectionParams(window.location.search, cloudReplica);
  const collectionSearch = collectionParams.toString();
  const issuePage = Number(collectionParams.get("page") ?? "1");
  const collectionFilters: ReviewIssueFilters = {
    ...(collectionParams.get("priority") ? { priority: collectionParams.get("priority")! } : {}),
    ...(collectionParams.get("failure_layer") ? { failure_layer: collectionParams.get("failure_layer")! } : {}),
    ...(collectionParams.get("owner") ? { owner: collectionParams.get("owner")! } : {}),
    ...(collectionParams.get("q") ? { query: collectionParams.get("q")! } : {}),
    ...(collectionParams.get("created_after") ? { created_after: collectionParams.get("created_after")! } : {}),
  };
  const reviewApi = useMemo(() => faeWorkbenchApi.review(account.csrf_token), [account.csrf_token]);
  const issueFilters = useMemo(() => ({
    ...collectionFilters,
    ...(!issueStatus ? {} : cloudReplica && issueStatus !== "open"
      ? { disposition: issueStatus } : { status: issueStatus }),
    limit: 200,
    offset: (issuePage - 1) * 200,
  }), [cloudReplica, collectionSearch, filterRevision, issuePage, issueStatus]);

  useEffect(() => {
    const restore = () => setFilterRevision((value) => value + 1);
    window.addEventListener("popstate", restore);
    window.addEventListener("platform:navigate", restore);
    return () => {
      window.removeEventListener("popstate", restore);
      window.removeEventListener("platform:navigate", restore);
    };
  }, []);

  useEffect(() => {
    if (!deploymentResolved || parsedIssueFilter.valid) return;
    const query = new URLSearchParams(window.location.search);
    query.delete("status");
    query.delete("disposition");
    query.delete("page");
    const search = query.toString();
    navigate(`${localPathname()}${search ? `?${search}` : ""}`, { replace: true });
  }, [cloudReplica, deploymentResolved, filterRevision, parsedIssueFilter.valid]);

  const changeIssueStatus = (status: string) => {
    const query = safeIssueCollectionParams(window.location.search, cloudReplica);
    query.delete("status");
    query.delete("disposition");
    query.delete("page");
    if (status === "open" || (status && !cloudReplica)) query.set("status", status);
    else if (status) query.set("disposition", status);
    const search = query.toString();
    navigate(`${localPathname()}${search ? `?${search}` : ""}`);
  };

  const changeCollectionFilters = (filters: ReviewIssueFilters) => {
    const next = safeIssueCollectionParams(window.location.search, cloudReplica);
    for (const [key, value] of Object.entries({
      priority: filters.priority, failure_layer: filters.failure_layer,
      owner: filters.owner, q: filters.query, created_after: filters.created_after,
    })) {
      if (value) next.set(key, String(value)); else next.delete(key);
    }
    next.delete("page");
    navigate(`${localPathname()}${next.size ? `?${next}` : ""}`);
  };

  const changeIssuePage = (page: number, replace = false) => {
    const next = safeIssueCollectionParams(window.location.search, cloudReplica);
    if (page > 1) next.set("page", String(page)); else next.delete("page");
    navigate(`${localPathname()}${next.size ? `?${next}` : ""}`, { replace });
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
  if (!deploymentResolved || !parsedIssueFilter.valid) content = <LoadingState label="正在确认部署模式" />;
  else if (loadingTurn) content = <LoadingState label="正在加载原始回答" />;
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
    onIssueFiltersChange={changeCollectionFilters}
    onIssuePageChange={changeIssuePage}
    collectionSearch={collectionSearch}
    statusOptions={cloudReplica ? CLOUD_STATUS_OPTIONS : LOCAL_STATUS_OPTIONS}
    statusPresentation={cloudReplica ? "disposition" : "lifecycle"}
    readOnlyReason={account.hard_stale_read_only ? "hard-stale" : undefined}
  />;

  return <FaeWorkbenchShell currentSection="issues">{content}</FaeWorkbenchShell>;
}
