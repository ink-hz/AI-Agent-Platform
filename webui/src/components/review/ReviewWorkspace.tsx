import { useEffect, useRef, useState } from "react";

import { ErrorState, LoadingState } from "../DataState";
import { navigate } from "../../router";
import type {
  FeedbackIssueDetail,
  FeedbackIssueSummary,
  IssueLink,
  ReplayRun,
  ReviewInboxItem,
  ReviewOverview,
  TurnClosureSummary,
} from "../../types";
import { IssueDetail } from "./IssueDetail";
import { IssueList, STATUS_LABELS, type IssueFilterOption } from "./IssueList";


export interface ReviewApi {
  overview(signal?: AbortSignal): Promise<ReviewOverview>;
  inbox(signal?: AbortSignal): Promise<ReviewInboxItem[]>;
  issues(signal?: AbortSignal, filters?: ReviewIssueFilters): Promise<FeedbackIssueSummary[] | ReviewIssuePage>;
  turnSummaries(turnKeys: string[], signal?: AbortSignal): Promise<TurnClosureSummary[]>;
  issue(id: string, signal?: AbortSignal): Promise<FeedbackIssueDetail>;
  create(payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  link(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  update(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  move(issueId: string, linkId: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  fixReady(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  merge(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  addEvidence(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  verifyEvidence(evidenceId: string, actor: string): Promise<FeedbackIssueDetail>;
  replay(issueId: string, payload: Record<string, unknown>, actor: string): Promise<ReplayRun>;
  semanticReview(replayId: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  disposition(issueId: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
}

export interface ReviewIssueFilters {
  status?: string;
  disposition?: string;
  priority?: string;
  failure_layer?: string;
  owner?: string;
  query?: string;
  created_after?: string;
  limit?: number;
  offset?: number;
}

export interface ReviewIssuePage {
  items: FeedbackIssueSummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ReviewWorkspaceProps {
  api: ReviewApi;
  agentId: string;
  basePath: string;
  initialIssueId: string | null;
  initialTurn: ReviewInboxItem | null;
  actor: string;
  showActorField: boolean;
  showAgentFilter: boolean;
  readOnlyReason?: "hard-stale";
  statusFilter?: string;
  onStatusFilterChange?: (status: string) => void;
  issueFilters?: ReviewIssueFilters;
  statusOptions?: IssueFilterOption[];
  statusPresentation?: "lifecycle" | "disposition";
  onIssueFiltersChange?: (filters: ReviewIssueFilters) => void;
  onIssuePageChange?: (page: number, replace?: boolean) => void;
  collectionSearch?: string;
}

type SelectionToken = {
  issueId: string | null;
  turnKey: string | null;
  generation: number;
};

type MutationToken = SelectionToken & {
  lifecycleEpoch: number;
};

const STATUS_ORDER = [
  "pending_triage", "fixing", "awaiting_merge", "awaiting_deploy",
  "awaiting_replay", "awaiting_review", "closed", "duplicate",
  "not_actionable", "wont_fix",
] as const;

function accountableActor(value: string): boolean {
  const selected = value.trim();
  return selected === "codex" || /^fae:\S+$/.test(selected) || /^corp:\S+$/.test(selected);
}

function apiStatus(error: unknown): number | null {
  if (!error || typeof error !== "object" || !("status" in error)) return null;
  return typeof error.status === "number" ? error.status : null;
}

export function ReviewWorkspace({
  api,
  agentId,
  basePath,
  initialIssueId,
  initialTurn,
  actor: initialActor,
  showActorField,
  showAgentFilter,
  readOnlyReason,
  statusFilter,
  onStatusFilterChange,
  issueFilters,
  statusOptions,
  statusPresentation,
  onIssueFiltersChange,
  onIssuePageChange,
  collectionSearch = "",
}: ReviewWorkspaceProps) {
  const requestedTurnKey = initialTurn?.turn_key ?? new URLSearchParams(window.location.search).get("turn_key");
  const [overview, setOverview] = useState<ReviewOverview | null>(null);
  const [issues, setIssues] = useState<FeedbackIssueSummary[]>([]);
  const [issuePage, setIssuePage] = useState<ReviewIssuePage | null>(null);
  const [inbox, setInbox] = useState<ReviewInboxItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(initialIssueId);
  const [selectedTurnKey, setSelectedTurnKey] = useState<string | null>(requestedTurnKey);
  const [detail, setDetail] = useState<FeedbackIssueDetail | null>(null);
  const [actor, setActor] = useState(initialActor);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [existingIssueId, setExistingIssueId] = useState("");
  const selectionRef = useRef<SelectionToken>({
    issueId: initialIssueId,
    turnKey: initialIssueId ? null : requestedTurnKey,
    generation: 0,
  });
  const skippedDetailSelectionRef = useRef<SelectionToken | null>(null);
  const lifecycleRef = useRef({ mounted: true, epoch: 0 });
  const refreshControllersRef = useRef(new Set<AbortController>());

  useEffect(() => {
    lifecycleRef.current.mounted = true;
    return () => {
      lifecycleRef.current.mounted = false;
      lifecycleRef.current.epoch += 1;
      refreshControllersRef.current.forEach((controller) => controller.abort());
      refreshControllersRef.current.clear();
    };
  }, []);

  const selectIssueState = (id: string | null, turnKey: string | null) => {
    if (selectionRef.current.issueId !== id || selectionRef.current.turnKey !== turnKey) {
      selectionRef.current = { issueId: id, turnKey, generation: selectionRef.current.generation + 1 };
    }
    const skipped = skippedDetailSelectionRef.current;
    if (skipped && (skipped.issueId !== id || skipped.turnKey !== turnKey || skipped.generation !== selectionRef.current.generation)) {
      skippedDetailSelectionRef.current = null;
    }
    setSelectedId(id);
    setSelectedTurnKey(turnKey);
  };

  useEffect(() => {
    if (!showActorField) setActor(initialActor);
  }, [initialActor, showActorField]);

  useEffect(() => {
    if (basePath === "/admin/review") return;
    selectIssueState(initialIssueId, initialIssueId ? null : initialTurn?.turn_key ?? null);
  }, [basePath, initialIssueId, initialTurn]);

  const loadLists = async (signal?: AbortSignal, expectedSelection?: MutationToken) => {
    const [nextOverview, nextInbox, nextIssues] = await Promise.all([
      api.overview(signal), api.inbox(signal), api.issues(signal, issueFilters),
    ]);
    if (signal?.aborted || (expectedSelection && !selectionIsCurrent(expectedSelection))) return;
    setOverview(nextOverview);
    setInbox(nextInbox);
    const normalized = Array.isArray(nextIssues)
      ? { items: nextIssues, total: nextIssues.length, limit: nextIssues.length || 1, offset: 0, has_more: false }
      : nextIssues;
    if (
      !Array.isArray(nextIssues)
      && normalized.offset > 0
      && normalized.items.length === 0
      && onIssuePageChange
    ) {
      const lastPage = Math.max(1, Math.ceil(normalized.total / normalized.limit));
      onIssuePageChange(lastPage, true);
      return;
    }
    setIssues(normalized.items);
    setIssuePage(normalized);
  };

  const loadDetail = async (id: string, signal?: AbortSignal, generation = selectionRef.current.generation) => {
    const value = await api.issue(id, signal);
    if (!signal?.aborted && selectionRef.current.issueId === id && selectionRef.current.generation === generation) setDetail(value);
    return value;
  };

  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    setOverview(null);
    void loadLists(controller.signal).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [api, agentId, issueFilters?.status, issueFilters?.disposition, issueFilters?.priority,
    issueFilters?.failure_layer, issueFilters?.owner, issueFilters?.query,
    issueFilters?.created_after, issueFilters?.limit, issueFilters?.offset]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    const generation = selectionRef.current.generation;
    const skipped = skippedDetailSelectionRef.current;
    if (skipped?.issueId === selectedId && skipped.generation === generation) {
      skippedDetailSelectionRef.current = null;
      return;
    }
    setDetail(null);
    const controller = new AbortController();
    void loadDetail(selectedId, controller.signal, generation).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [api, selectedId]);

  useEffect(() => setExistingIssueId(""), [selectedTurnKey]);

  const genericPath = (params: Record<string, string>) => {
    const query = new URLSearchParams({ agent_id: agentId, ...params });
    return `${basePath}?${query}`;
  };

  const chooseIssue = (id: string, refreshDetailInMutation = false): SelectionToken => {
    selectIssueState(id, null);
    const selection = { ...selectionRef.current };
    if (refreshDetailInMutation) skippedDetailSelectionRef.current = selection;
    if (basePath === "/admin/review") navigate(genericPath({ issue: id }), { replace: true });
    else navigate(`${basePath}/${encodeURIComponent(id)}${collectionSearch ? `?${collectionSearch}` : ""}`);
    return selection;
  };

  const chooseInbox = (turnKey: string) => {
    selectIssueState(null, turnKey);
    if (basePath === "/admin/review") navigate(genericPath({ turn_key: turnKey }), { replace: true });
    else {
      const current = new URLSearchParams(window.location.search);
      const sessionKey = initialTurn?.turn_key === turnKey ? current.get("session_key") : null;
      const query = new URLSearchParams({ ...(sessionKey ? { session_key: sessionKey } : {}), turn_key: turnKey });
      navigate(`${basePath}?${query}`, { replace: true });
    }
  };

  const saveActor = (value: string) => {
    setActor(value);
    try { sessionStorage.setItem("reviewActor", value); } catch { /* session-only identity */ }
  };

  const requireActor = () => {
    if (!accountableActor(actor)) throw new Error("请先填写 codex、fae:<姓名> 或 corp:<账号> 作为可追责身份");
    return actor.trim();
  };

  const currentSelection = (): MutationToken => ({
    ...selectionRef.current,
    lifecycleEpoch: lifecycleRef.current.epoch,
  });

  const lifecycleIsCurrent = (selection: MutationToken) => (
    lifecycleRef.current.mounted && lifecycleRef.current.epoch === selection.lifecycleEpoch
  );

  const selectionIsCurrent = (selection: MutationToken) => (
    lifecycleIsCurrent(selection)
    && selectionRef.current.issueId === selection.issueId
    && selectionRef.current.turnKey === selection.turnKey
    && selectionRef.current.generation === selection.generation
  );

  const trackedRefreshController = () => {
    const controller = new AbortController();
    refreshControllersRef.current.add(controller);
    return controller;
  };

  const handleError = async (error: unknown, selection: MutationToken) => {
    if (!selectionIsCurrent(selection)) return;
    if (apiStatus(error) === 409) {
      const controller = trackedRefreshController();
      try {
        if (selection.issueId) await loadDetail(selection.issueId, controller.signal, selection.generation);
      } catch (refreshError) {
        if (selectionIsCurrent(selection)) {
          setMessage(refreshError instanceof Error ? refreshError.message : "冲突状态刷新失败，请稍后重试。");
        }
        return;
      } finally {
        refreshControllersRef.current.delete(controller);
      }
      if (!selectionIsCurrent(selection)) return;
      setMessage("记录已被其他复审者更新；已刷新最新状态，未提交文本仍保留在表单中。");
      return;
    }
    setMessage(error instanceof Error ? error.message : "操作失败，请查看服务状态。");
  };

  const perform = async <T,>(
    operation: (identity: string) => Promise<T>,
    success: string,
    successIssueId?: (result: T) => string,
  ) => {
    const selection = currentSelection();
    let errorSelection = selection;
    setBusy(true);
    setMessage("");
    try {
      const result = await operation(requireActor());
      if (!selectionIsCurrent(selection)) return;
      if (successIssueId) {
        const intendedIssueId = successIssueId(result);
        chooseIssue(intendedIssueId, true);
        const intendedSelection = currentSelection();
        errorSelection = intendedSelection;
        const controller = trackedRefreshController();
        try {
          await Promise.all([
            loadDetail(intendedIssueId, controller.signal, intendedSelection.generation),
            loadLists(controller.signal, intendedSelection),
          ]);
        } finally {
          refreshControllersRef.current.delete(controller);
        }
        if (!selectionIsCurrent(intendedSelection)) return;
        setMessage(success);
        return;
      }
      const controller = trackedRefreshController();
      try {
        if (selection.issueId) await loadDetail(selection.issueId, controller.signal, selection.generation);
        if (!selectionIsCurrent(selection)) return;
        await loadLists(controller.signal, selection);
      } finally {
        refreshControllersRef.current.delete(controller);
      }
      if (!selectionIsCurrent(selection)) return;
      setMessage(success);
    } catch (error) {
      await handleError(error, errorSelection);
    } finally {
      if (lifecycleIsCurrent(selection)) setBusy(false);
    }
  };

  const workspaceInbox = initialTurn && !inbox.some((item) => item.turn_key === initialTurn.turn_key)
    ? [initialTurn, ...inbox]
    : inbox;
  const selectedInbox = initialTurn?.turn_key === selectedTurnKey
    ? initialTurn
    : workspaceInbox.find((item) => item.turn_key === selectedTurnKey) || null;

  if (failed) return <ErrorState />;
  if (!overview) return <LoadingState label="正在加载反馈闭环" />;

  const hardStaleReadOnly = readOnlyReason === "hard-stale";
  const replicaReadOnly = !overview.write_available && basePath === "/admin/fae/issues";
  const readOnly = !overview.write_available || hardStaleReadOnly;
  const hideMutations = replicaReadOnly || hardStaleReadOnly;

  return <>
    <section className="review-hero"><div><p>Feedback Repair Ledger</p><h1>反馈修复闭环</h1><span>状态由合并、部署、逐题真实复跑和独立语义复审证据自动计算。</span></div>{showActorField && <label>复审身份<input value={actor} onChange={(event) => saveActor(event.target.value)} placeholder="codex / fae:zhangsan" aria-invalid={actor.length > 0 && !accountableActor(actor)} /><small>仅保存在当前浏览器 session，不使用 web-reviewer。</small></label>}</section>
    {readOnly && <div className="review-message" role="status">{hardStaleReadOnly
      ? "通讯录状态已超过安全时限，治理变更已暂停。"
      : replicaReadOnly
        ? "当前为只读副本"
        : "只读模式：Writer 当前不可用，原始反馈、事项和最新复测答案仍可查看，所有写操作已禁用。"}</div>}
    {message && <div className="review-message" role="status">{message}</div>}
    <section className="review-overview"><article><span>反馈总行数</span><strong>{overview.feedback_rows ?? "暂不可用"}</strong></article><article><span>负反馈回答</span><strong>{overview.negative_turns ?? "暂不可用"}</strong><small>{overview.negative_rows === null ? "负反馈记录暂不可用" : `${overview.negative_rows} 条负反馈记录`}</small></article>{overview.lifecycle_status_available === false
      ? <>{Object.entries(overview.dispositions).map(([disposition, count]) => <article key={disposition}><span>{disposition === "actionable" ? "可处理事项" : STATUS_LABELS[disposition as keyof typeof STATUS_LABELS] || disposition}</span><strong>{count}</strong></article>)}<article><span>生命周期状态</span><strong>暂不可用</strong></article></>
      : STATUS_ORDER.map((status) => <article key={status}><span>{STATUS_LABELS[status]}</span><strong>{overview.statuses[status] ?? 0}</strong></article>)}</section>
    <section className="review-workspace"><div><IssueList issues={issues} inbox={workspaceInbox} selectedId={selectedId} selectedTurnKey={selectedTurnKey} onSelect={chooseIssue} onSelectInbox={chooseInbox} showAgentFilter={showAgentFilter} statusFilter={statusFilter} onStatusFilterChange={onStatusFilterChange} statusOptions={statusOptions} statusPresentation={statusPresentation} serverFilters={onIssueFiltersChange ? issueFilters : undefined} onServerFiltersChange={onIssueFiltersChange} />{issuePage && onIssuePageChange && <nav className="review-pagination" aria-label="Issue 分页"><button type="button" disabled={issuePage.offset === 0} onClick={() => onIssuePageChange(Math.max(1, Math.floor(issuePage.offset / issuePage.limit)))}>上一页</button><span>第 {Math.floor(issuePage.offset / issuePage.limit) + 1} 页 · 共 {issuePage.total} 项</span><button type="button" disabled={!issuePage.has_more} onClick={() => onIssuePageChange(Math.floor(issuePage.offset / issuePage.limit) + 2)}>下一页</button></nav>}</div><section className="review-main-panel" aria-label="事项详情">
      {detail && <IssueDetail detail={detail} busy={busy || (readOnly && !hideMutations)} readOnly={hideMutations}
        issues={issues}
        onSave={(owner, failureLayer, priority, rootCause, impactScope) => perform((identity) => api.update(detail.issue.id, { row_version: detail.issue.row_version, owner: owner || null, failure_layer: failureLayer || null, priority, root_cause: rootCause, impact_scope: impactScope, reason: "update triage" }, identity), "归因已保存，状态已重新计算。")}
        onFixReady={() => perform((identity) => api.fixReady(detail.issue.id, { row_version: detail.issue.row_version, reason: "implementation and tests ready" }, identity), "修复准备证据已记录。")}
        onEvidence={(payload) => perform((identity) => api.addEvidence(detail.issue.id, payload, identity), "工程证据已添加，等待机器验证。")}
        onVerify={(id) => perform((identity) => api.verifyEvidence(id, identity), "机器验证完成，状态已重新计算。")}
        onReplay={(link: IssueLink) => perform((identity) => api.replay(detail.issue.id, { issue_link_id: link.id, idempotency_key: `${link.id}-${Date.now()}` }, identity), "真实复跑完成，请查看最新答案与 runtime gate。")}
        onMove={(link: IssueLink, targetIssueId: string) => perform((identity) => api.move(detail.issue.id, link.id, { target_issue_id: targetIssueId, reason: "correct feedback issue grouping" }, identity), "回答归属已移动，源事项和目标事项状态均已重新计算。")}
        onReview={(replay: ReplayRun, verdict, reason) => perform((identity) => {
          if (!reason.trim()) throw new Error("语义复审必须填写理由");
          return api.semanticReview(replay.id, { verdict, method: identity === "codex" ? "codex" : "human_fae", reviewer: identity, reason }, identity);
        }, "独立语义复审已记录，状态已重新计算。")}
        onDisposition={(value, target, reason) => perform((identity) => value === "duplicate"
          ? api.merge(detail.issue.id, { target_issue_id: target, row_version: detail.issue.row_version, reason }, identity)
          : api.disposition(detail.issue.id, { disposition: value, canonical_issue_id: null, owner: detail.issue.owner, row_version: detail.issue.row_version, reason }, identity), "处置结果已记录并单列统计。")}
      />}
      {!detail && selectedInbox && <section className="review-empty-detail"><p>待纳管回答</p><h2>{selectedInbox.question || "未记录问题"}</h2><div><strong>原回答</strong><p>{selectedInbox.answer || "未记录原回答"}</p></div>{!hideMutations && <><div className="review-inbox-actions"><label>关联到已有事项<select aria-label="已有事项" value={existingIssueId} onChange={(event) => setExistingIssueId(event.target.value)}><option value="">选择 canonical issue</option>{issues.filter((item) => item.disposition === "actionable").map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><button disabled={busy || readOnly || !existingIssueId} onClick={() => perform(async (identity) => {
        await api.link(existingIssueId, { agent_id: selectedInbox.agent_id, source_turn_key: selectedInbox.turn_key, source_feedback_keys: selectedInbox.feedback_keys, link_role: "primary", reason: "link negative feedback turn to existing canonical issue" }, identity);
        return existingIssueId;
      }, "负反馈回答已关联到已有事项。", (issueId) => issueId)} >关联到已有事项</button><span>或</span></div><button disabled={busy || readOnly} onClick={() => perform(async (identity) => {
        const created = await api.create({ agent_id: selectedInbox.agent_id, origin_turn_key: selectedInbox.turn_key, title: (selectedInbox.question || selectedInbox.turn_key).slice(0, 80), priority: "P2", reason: "create from negative feedback inbox" }, identity);
        await api.link(created.issue.id, { agent_id: selectedInbox.agent_id, source_turn_key: selectedInbox.turn_key, source_feedback_keys: selectedInbox.feedback_keys, link_role: "primary", reason: "link negative feedback turn" }, identity);
        return created.issue.id;
      }, "负反馈回答已纳入闭环。", (issueId) => issueId)} >创建事项并纳管</button></>}</section>}
      {!detail && !selectedInbox && <section className="review-empty-detail"><p>选择左侧事项</p><h2>查看根因、证据、复跑答案与审计历史</h2><span>系统不提供手工“关闭”动作；只有全部硬门满足才会自动闭环。</span></section>}
    </section></section>
  </>;
}
