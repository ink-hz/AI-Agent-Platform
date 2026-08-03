import { useEffect, useMemo, useState } from "react";

import {
  ReviewApiError,
  addFixEvidence,
  createReviewIssue,
  fetchReviewInbox,
  fetchReviewIssue,
  fetchReviewIssues,
  fetchReviewOverview,
  linkReviewTurn,
  markFixReady,
  mergeReviewIssue,
  moveReviewLink,
  reviewReplay,
  setIssueDisposition,
  startReplay,
  updateReviewIssue,
  verifyFixEvidence,
} from "../api";
import { ErrorState, LoadingState } from "../components/DataState";
import { IssueDetail } from "../components/review/IssueDetail";
import { IssueList, STATUS_LABELS } from "../components/review/IssueList";
import type {
  FeedbackIssueDetail,
  FeedbackIssueSummary,
  IssueLink,
  ReplayRun,
  ReviewInboxItem,
  ReviewOverview,
} from "../types";


function initialActor(): string {
  try { return sessionStorage.getItem("reviewActor") || ""; } catch { return ""; }
}


function accountableActor(value: string): boolean {
  const actor = value.trim();
  return actor === "codex" || /^fae:\S+$/.test(actor) || /^corp:\S+$/.test(actor);
}


const STATUS_ORDER = [
  "pending_triage", "fixing", "awaiting_merge", "awaiting_deploy",
  "awaiting_replay", "awaiting_review", "closed", "duplicate",
  "not_actionable", "wont_fix",
] as const;


export function ReviewPage() {
  const query = useMemo(() => new URLSearchParams(window.location.search), []);
  const [overview, setOverview] = useState<ReviewOverview | null>(null);
  const [issues, setIssues] = useState<FeedbackIssueSummary[]>([]);
  const [inbox, setInbox] = useState<ReviewInboxItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(query.get("issue"));
  const [selectedTurnKey, setSelectedTurnKey] = useState<string | null>(query.get("turn_key"));
  const [detail, setDetail] = useState<FeedbackIssueDetail | null>(null);
  const [actor, setActor] = useState(initialActor);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [existingIssueId, setExistingIssueId] = useState("");

  const loadLists = async (signal?: AbortSignal) => {
    const [nextOverview, nextInbox, nextIssues] = await Promise.all([
      fetchReviewOverview(signal), fetchReviewInbox(signal), fetchReviewIssues(signal),
    ]);
    setOverview(nextOverview);
    setInbox(nextInbox);
    setIssues(nextIssues);
  };
  const loadDetail = async (id: string, signal?: AbortSignal) => {
    const value = await fetchReviewIssue(id, signal);
    setDetail(value);
    return value;
  };

  useEffect(() => {
    const controller = new AbortController();
    loadLists(controller.signal).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    const controller = new AbortController();
    loadDetail(selectedId, controller.signal).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [selectedId]);
  useEffect(() => setExistingIssueId(""), [selectedTurnKey]);

  const chooseIssue = (id: string) => {
    setSelectedId(id);
    setSelectedTurnKey(null);
    window.history.replaceState({}, "", `/review?issue=${encodeURIComponent(id)}`);
  };
  const chooseInbox = (turnKey: string) => {
    setSelectedId(null);
    setSelectedTurnKey(turnKey);
    window.history.replaceState({}, "", `/review?turn_key=${encodeURIComponent(turnKey)}`);
  };
  const saveActor = (value: string) => {
    setActor(value);
    try { sessionStorage.setItem("reviewActor", value); } catch { /* session-only identity */ }
  };
  const requireActor = () => {
    if (!accountableActor(actor)) throw new Error("请先填写 codex、fae:<姓名> 或 corp:<账号> 作为可追责身份");
    return actor.trim();
  };
  const handleError = async (error: unknown) => {
    if (error instanceof ReviewApiError && error.status === 409) {
      setMessage("记录已被其他复审者更新；已刷新最新状态，未提交文本仍保留在表单中。");
      if (selectedId) await loadDetail(selectedId);
      return;
    }
    setMessage(error instanceof Error ? error.message : "操作失败，请查看服务状态。");
  };
  const perform = async (operation: (identity: string) => Promise<unknown>, success: string) => {
    setBusy(true);
    setMessage("");
    try {
      await operation(requireActor());
      if (selectedId) await loadDetail(selectedId);
      await loadLists();
      setMessage(success);
    } catch (error) {
      await handleError(error);
    } finally {
      setBusy(false);
    }
  };

  const selectedInbox = inbox.find((item) => item.turn_key === selectedTurnKey) || null;
  if (failed) return <ErrorState />;
  if (!overview) return <LoadingState label="正在加载反馈闭环" />;

  return <>
    <section className="review-hero"><div><p>Feedback Repair Ledger</p><h1>反馈修复闭环</h1><span>状态由合并、部署、逐题真实复跑和独立语义复审证据自动计算。</span></div><label>复审身份<input value={actor} onChange={(event) => saveActor(event.target.value)} placeholder="codex / fae:zhangsan" aria-invalid={actor.length > 0 && !accountableActor(actor)} /><small>仅保存在当前浏览器 session，不使用 web-reviewer。</small></label></section>
    {message && <div className="review-message" role="status">{message}</div>}
    <section className="review-overview"><article><span>反馈总行数</span><strong>{overview.feedback_rows}</strong></article><article><span>负反馈回答</span><strong>{overview.negative_turns}</strong><small>{overview.negative_rows} 条负反馈记录</small></article>{STATUS_ORDER.map((status) => <article key={status}><span>{STATUS_LABELS[status]}</span><strong>{overview.statuses[status] || 0}</strong></article>)}</section>
    <section className="review-workspace"><IssueList issues={issues} inbox={inbox} selectedId={selectedId} selectedTurnKey={selectedTurnKey} onSelect={chooseIssue} onSelectInbox={chooseInbox} /><main className="review-main-panel">
      {detail && <IssueDetail detail={detail} busy={busy}
        issues={issues}
        onSave={(owner, failureLayer, priority, rootCause, impactScope) => perform((identity) => updateReviewIssue(detail.issue.id, { row_version: detail.issue.row_version, owner: owner || null, failure_layer: failureLayer || null, priority, root_cause: rootCause, impact_scope: impactScope, reason: "update triage" }, identity), "归因已保存，状态已重新计算。")}
        onFixReady={() => perform((identity) => markFixReady(detail.issue.id, { row_version: detail.issue.row_version, reason: "implementation and tests ready" }, identity), "修复准备证据已记录。")}
        onEvidence={(payload) => perform((identity) => addFixEvidence(detail.issue.id, payload, identity), "工程证据已添加，等待机器验证。")}
        onVerify={(id) => perform((identity) => verifyFixEvidence(id, identity), "机器验证完成，状态已重新计算。")}
        onReplay={(link: IssueLink) => perform((identity) => startReplay(detail.issue.id, { issue_link_id: link.id, idempotency_key: `${link.id}-${Date.now()}` }, identity), "真实复跑完成，请查看最新答案与 runtime gate。")}
        onMove={(link: IssueLink, targetIssueId: string) => perform((identity) => moveReviewLink(detail.issue.id, link.id, { target_issue_id: targetIssueId, reason: "correct feedback issue grouping" }, identity), "回答归属已移动，源事项和目标事项状态均已重新计算。")}
        onReview={(replay: ReplayRun, verdict, reason) => perform((identity) => {
          if (!reason.trim()) throw new Error("语义复审必须填写理由");
          return reviewReplay(replay.id, { verdict, method: identity === "codex" ? "codex" : "human_fae", reviewer: identity, reason }, identity);
        }, "独立语义复审已记录，状态已重新计算。")}
        onDisposition={(value, target, reason) => perform((identity) => value === "duplicate"
          ? mergeReviewIssue(detail.issue.id, { target_issue_id: target, row_version: detail.issue.row_version, reason }, identity)
          : setIssueDisposition(detail.issue.id, { disposition: value, canonical_issue_id: null, owner: detail.issue.owner, row_version: detail.issue.row_version, reason }, identity), "处置结果已记录并单列统计。")}
      />}
      {!detail && selectedInbox && <section className="review-empty-detail"><p>待纳管负反馈</p><h2>{selectedInbox.question || "未记录问题"}</h2><div><strong>原回答</strong><p>{selectedInbox.answer || "未记录原回答"}</p></div><div className="review-inbox-actions"><label>关联到已有事项<select aria-label="已有事项" value={existingIssueId} onChange={(event) => setExistingIssueId(event.target.value)}><option value="">选择 canonical issue</option>{issues.filter((item) => item.disposition === "actionable").map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><button disabled={busy || !existingIssueId} onClick={() => perform(async (identity) => {
        await linkReviewTurn(existingIssueId, { agent_id: selectedInbox.agent_id, source_turn_key: selectedInbox.turn_key, source_feedback_keys: selectedInbox.feedback_keys, link_role: "primary", reason: "link negative feedback turn to existing canonical issue" }, identity);
        chooseIssue(existingIssueId);
      }, "负反馈回答已关联到已有事项。")}>关联到已有事项</button><span>或</span></div><button disabled={busy} onClick={() => perform(async (identity) => {
        const created = await createReviewIssue({ agent_id: selectedInbox.agent_id, origin_turn_key: selectedInbox.turn_key, title: (selectedInbox.question || selectedInbox.turn_key).slice(0, 80), priority: "P2", reason: "create from negative feedback inbox" }, identity);
        await linkReviewTurn(created.issue.id, { agent_id: selectedInbox.agent_id, source_turn_key: selectedInbox.turn_key, source_feedback_keys: selectedInbox.feedback_keys, link_role: "primary", reason: "link negative feedback turn" }, identity);
        chooseIssue(created.issue.id);
      }, "负反馈回答已纳入闭环。")}>创建事项并纳管</button></section>}
      {!detail && !selectedInbox && <section className="review-empty-detail"><p>选择左侧事项</p><h2>查看根因、证据、复跑答案与审计历史</h2><span>系统不提供手工“关闭”动作；只有全部硬门满足才会自动闭环。</span></section>}
    </main></section>
  </>;
}
