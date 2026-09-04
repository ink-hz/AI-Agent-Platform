import { useEffect, useMemo, useRef, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type {
  HrCandidate, HrCandidateAnalysisVersion, HrCandidateDocument, HrCandidateDraft,
  HrHumanFeedback, HrPositionCandidate, HrTaskRecord,
} from "../../hrR12Types";
import {
  AttachmentUploader, type AttachmentUploadClient, type UploadQueueItem,
} from "../../components/conversation/AttachmentUploader";

type CandidateApi = Pick<HrR12Api,
  "candidateDrafts" | "retryDraft" | "confirmDraft" | "createCandidateDraftBatch"
  | "positionCandidates" | "candidate" | "candidateDocuments" | "candidateAnalyses"
  | "candidateFeedback" | "appendCandidateFeedback" | "compareCandidates"
  | "startTask" | "activeTasks">;
type NamedRelation = { relation: HrPositionCandidate; candidate: HrCandidate };
type CandidateDetail = NamedRelation & { documents: HrCandidateDocument[]; analyses: HrCandidateAnalysisVersion[]; feedback: HrHumanFeedback[] };
type DraftEdit = { stableName: string; facts: string; mergeCandidateId: string | null | undefined };

const STATE_LABEL: Record<HrCandidateDraft["state"], string> = {
  pending: "等待解析", processing: "正在解析", ready: "待确认", failed: "解析失败",
  confirmed: "已确认", dismissed: "已忽略",
};
const TASK_LABEL = { candidate_match: "匹配分析", candidate_interview_plan: "候选人专属面试题" } as const;
const MAX_DRAFT_POLL_ATTEMPTS = 6;

function extractedName(draft: HrCandidateDraft): string {
  const value = draft.extractedFacts.stable_name;
  return typeof value === "string" && value.trim() ? value.trim() : `候选人 ${draft.attachmentId.slice(0, 8)}`;
}
function resultText(value: Record<string, unknown>): string {
  const summary = value.summary;
  return typeof summary === "string" ? summary : JSON.stringify(value, null, 2);
}
function evidenceText(value: Record<string, unknown>): string {
  return typeof value.claim === "string" ? value.claim : resultText(value);
}
function newestAnalysis(items: HrCandidateAnalysisVersion[]): HrCandidateAnalysisVersion | null {
  return items.reduce<HrCandidateAnalysisVersion | null>((latest, item) => !latest || item.versionNumber > latest.versionNumber ? item : latest, null);
}
function draftEdit(draft: HrCandidateDraft): DraftEdit {
  return { stableName: extractedName(draft), facts: JSON.stringify(draft.extractedFacts, null, 2), mergeCandidateId: draft.identityCandidateIds.length === 0 ? null : undefined };
}
function taskStatus(status: HrTaskRecord["status"]): string {
  return status === "accepted" ? "已受理" : status === "running" ? "执行中" : status === "completed" ? "已完成" : "执行失败";
}

export function HrCandidateWorkspace({ api, positionId, csrfToken, currentContextVersionId, uploadClient, readOnly = false }: {
  api: CandidateApi; positionId: string; csrfToken: string;
  currentContextVersionId: string | null; uploadClient?: AttachmentUploadClient; readOnly?: boolean;
}) {
  const [drafts, setDrafts] = useState<HrCandidateDraft[]>([]);
  const [relations, setRelations] = useState<NamedRelation[]>([]);
  const [queue, setQueue] = useState<UploadQueueItem[]>([]);
  const [selected, setSelected] = useState<CandidateDetail | null>(null);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<HrCandidateAnalysisVersion | null>(null);
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, DraftEdit>>({});
  const [correction, setCorrection] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [analysisTask, setAnalysisTask] = useState<HrTaskRecord | null>(null);
  const mutation = useRef<AbortController | null>(null);
  const draftPollAttempt = useRef(0);
  const retainedAnalysisTask = useRef<{ signature: string; requestId: string } | null>(null);

  async function load(signal?: AbortSignal) {
    const [nextDrafts, nextRelations] = await Promise.all([api.candidateDrafts(positionId, signal), api.positionCandidates(positionId, signal)]);
    const named = await Promise.all(nextRelations.map(async (relation) => ({ relation, candidate: await api.candidate(relation.candidateId, signal) })));
    if (!signal?.aborted) { setDrafts(nextDrafts); setRelations(named); }
  }
  useEffect(() => {
    draftPollAttempt.current = 0; retainedAnalysisTask.current = null;
    const controller = new AbortController(); setNotice(null); setSelected(null); setComparisonIds([]); setComparison(null);
    void load(controller.signal).catch(() => { if (!controller.signal.aborted) setNotice("候选人数据暂时不可用"); });
    return () => { controller.abort(); mutation.current?.abort(); };
  }, [api, positionId]);

  const processing = drafts.some((draft) => draft.state === "pending" || draft.state === "processing");
  useEffect(() => {
    if (!processing) { draftPollAttempt.current = 0; return; }
    if (draftPollAttempt.current >= MAX_DRAFT_POLL_ATTEMPTS) return;
    const controller = new AbortController();
    const delay = Math.min(1_000 * (2 ** draftPollAttempt.current), 8_000);
    const timeout = window.setTimeout(() => {
      void api.candidateDrafts(positionId, controller.signal).then((items) => {
        if (!controller.signal.aborted) {
          draftPollAttempt.current += 1;
          setDrafts(items);
          if (draftPollAttempt.current >= MAX_DRAFT_POLL_ATTEMPTS && items.some((item) => item.state === "pending" || item.state === "processing")) {
            setNotice("自动刷新已暂停，请使用“刷新候选人状态”继续检查。");
          }
        }
      }).catch(() => { if (!controller.signal.aborted) setNotice("解析状态暂时无法刷新，可手动重试。"); });
    }, delay);
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [api, positionId, processing, drafts]);

  useEffect(() => {
    setComparisonIds((ids) => ids.filter((id) => relations.some((item) => item.relation.positionCandidateId === id && item.relation.contextVersionId === currentContextVersionId)));
  }, [currentContextVersionId, relations]);

  useEffect(() => {
    if (!analysisTask || !selected || !["accepted", "running"].includes(analysisTask.status)) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      void api.activeTasks(positionId, controller.signal).then(async (tasks) => {
        if (controller.signal.aborted) return;
        const active = tasks.find((item) => item.taskId === analysisTask.taskId);
        if (active) { setAnalysisTask(active); return; }
        const analyses = await api.candidateAnalyses(selected.relation.positionCandidateId, controller.signal);
        if (!controller.signal.aborted) { setSelected((value) => value ? { ...value, analyses } : value); setAnalysisTask({ ...analysisTask, status: "completed" }); setNotice(`${TASK_LABEL[analysisTask.taskKind as keyof typeof TASK_LABEL]}已完成，分析版本已刷新。`); }
      }).catch(() => { if (!controller.signal.aborted) setNotice("候选人任务状态暂时无法刷新，可手动刷新分析。"); });
    }, 1_000);
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [analysisTask, api, positionId, selected]);

  function controller(): AbortController { mutation.current?.abort(); const next = new AbortController(); mutation.current = next; return next; }
  async function refresh() { const current = controller(); try { await load(current.signal); if (!current.signal.aborted) setNotice("候选人状态已刷新。"); } catch { if (!current.signal.aborted) setNotice("候选人状态暂时无法刷新。"); } }
  async function createBatch() {
    if (readOnly) return;
    const attachmentIds = queue.filter((item) => item.state === "ready" && item.attachment).map((item) => item.attachment!.attachmentId);
    if (attachmentIds.length === 0) return;
    const current = controller(); setNotice("正在提交简历解析…");
    try { const batch = await api.createCandidateDraftBatch(positionId, attachmentIds, crypto.randomUUID(), current.signal); if (!current.signal.aborted) { setDrafts((items) => [...batch.items, ...items.filter((item) => !batch.items.some((added) => added.draftId === item.draftId))]); setNotice(`已提交 ${batch.items.length} 份简历，正在自动刷新解析状态。`); } } catch { if (!current.signal.aborted) setNotice("简历批量解析未启动，可以安全重试。"); }
  }
  async function retry(draft: HrCandidateDraft) {
    if (readOnly) return;
    const current = controller();
    try { const next = await api.retryDraft(draft.draftId, draft.rowVersion, crypto.randomUUID(), current.signal); if (!current.signal.aborted) setDrafts((items) => items.map((item) => item.draftId === draft.draftId ? next : item)); } catch { if (!current.signal.aborted) setNotice("重试解析未完成，请重试。"); }
  }
  function review(draft: HrCandidateDraft) { setEdits((current) => current[draft.draftId] ? current : { ...current, [draft.draftId]: draftEdit(draft) }); setEditingDraftId(draft.draftId); }
  async function confirm(draft: HrCandidateDraft) {
    if (readOnly) return;
    if (!currentContextVersionId) { setNotice("请先确认岗位上下文，再确认候选人。"); return; }
    const edit = edits[draft.draftId] ?? draftEdit(draft);
    if (edit.mergeCandidateId === undefined) { setNotice("请选择新建候选人或明确合并对象。"); return; }
    let confirmedFacts: Record<string, unknown>;
    try { const value: unknown = JSON.parse(edit.facts); if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(); confirmedFacts = value as Record<string, unknown>; }
    catch { setNotice("候选人事实必须是有效的 JSON 对象。"); return; }
    const current = controller();
    try {
      const created = await api.confirmDraft(draft.draftId, { expectedRowVersion: draft.rowVersion, contextVersionId: currentContextVersionId, stableName: edit.stableName.trim(), confirmedFacts, mergeCandidateId: edit.mergeCandidateId }, crypto.randomUUID(), current.signal);
      if (!current.signal.aborted) { setDrafts((items) => items.map((item) => item.draftId === draft.draftId ? { ...item, state: "confirmed" } : item)); setRelations((items) => [...items.filter((item) => item.relation.positionCandidateId !== created.positionCandidate.positionCandidateId), { relation: created.positionCandidate, candidate: created.candidate }]); setEditingDraftId(null); setNotice("候选人已确认，AI 提取事实与人工确认记录保持分离。"); }
    } catch (error) {
      if (current.signal.aborted) return;
      if ((error as { status?: number }).status === 409) { setNotice("身份或版本已变化，已保留编辑内容；刷新状态后可重试。"); try { const fresh = await api.candidateDrafts(positionId, current.signal); if (!current.signal.aborted) setDrafts(fresh); } catch { /* retained edit remains available */ } }
      else setNotice("候选人确认未完成，请核对输入后重试。");
    }
  }
  async function openCandidate(item: NamedRelation) {
    const current = controller(); setNotice(null);
    try { const [documents, analyses, feedback] = await Promise.all([api.candidateDocuments(item.candidate.candidateId, current.signal), api.candidateAnalyses(item.relation.positionCandidateId, current.signal), api.candidateFeedback(item.relation.positionCandidateId, current.signal)]); if (!current.signal.aborted) setSelected({ ...item, documents, analyses, feedback }); }
    catch { if (!current.signal.aborted) setNotice("候选人详情暂时不可用"); }
  }
  async function refreshAnalyses() {
    if (!selected) return; const current = controller();
    try { const analyses = await api.candidateAnalyses(selected.relation.positionCandidateId, current.signal); if (!current.signal.aborted) { setSelected((value) => value ? { ...value, analyses } : value); setNotice("分析版本已刷新。"); } } catch { if (!current.signal.aborted) setNotice("分析版本暂时无法刷新。"); }
  }
  async function launch(kind: "candidate_match" | "candidate_interview_plan") {
    if (readOnly || !selected || !currentContextVersionId || selected.relation.contextVersionId !== currentContextVersionId) return;
    const current = controller();
    const input = { contextVersionId: currentContextVersionId, candidate: { candidateId: selected.candidate.candidateId, positionCandidateId: selected.relation.positionCandidateId }, materialIds: [] };
    const signature = JSON.stringify([kind, input]);
    const requestId = retainedAnalysisTask.current?.signature === signature
      ? retainedAnalysisTask.current.requestId : crypto.randomUUID();
    retainedAnalysisTask.current = { signature, requestId };
    try { const task = await api.startTask(positionId, kind, requestId, input, current.signal); if (!current.signal.aborted) { retainedAnalysisTask.current = null; setAnalysisTask(task); setNotice(`${TASK_LABEL[kind]}已启动，完成后将自动刷新分析版本。`); } } catch { if (!current.signal.aborted) setNotice("候选人任务未启动，可以安全重试。"); }
  }
  async function appendFeedback() {
    if (readOnly) return;
    const latest = selected ? newestAnalysis(selected.analyses) : null; if (!selected || !latest || !correction.trim()) return;
    const current = controller();
    try { const saved = await api.appendCandidateFeedback(selected.relation.positionCandidateId, { analysisVersionId: latest.analysisVersionId, feedbackKind: "correction", conclusionKey: "overall", correction: correction.trim(), reason: "HR 人工核实" }, crypto.randomUUID(), current.signal); if (!current.signal.aborted) { setSelected((value) => value ? { ...value, feedback: [saved, ...value.feedback] } : value); setCorrection(""); setNotice("人工纠正已单独记录，后续分析会引用但不会改写旧 AI 版本。"); } } catch { if (!current.signal.aborted) setNotice("人工纠正未保存，请重试。"); }
  }
  async function compare() {
    if (readOnly || !currentContextVersionId || comparisonIds.length < 2) return;
    const current = controller();
    try { const result = await api.compareCandidates(positionId, comparisonIds, currentContextVersionId, crypto.randomUUID(), current.signal); if (!current.signal.aborted) { setComparison(result); setNotice(`候选人比较已生成：分析版本 v${result.versionNumber}`); } } catch (error) { if (!current.signal.aborted) setNotice((error as { status?: number }).status === 409 ? "已选候选人的上下文版本已变化，请刷新后重算。" : "候选人比较未完成，请重试。"); }
  }

  const readyUploads = queue.filter((item) => item.state === "ready" && item.attachment).length;
  const orderedAnalyses = useMemo(() => [...(selected?.analyses ?? [])].sort((left, right) => right.versionNumber - left.versionNumber), [selected?.analyses]);
  const renderAnalysis = (item: HrCandidateAnalysisVersion) => <article key={item.analysisVersionId}><strong>分析版本 v{item.versionNumber}</strong><span>{item.analysisKind === "match" ? "岗位匹配" : item.analysisKind === "candidate_interview_plan" ? "专属面试题" : item.analysisKind === "comparison" ? "候选人比较" : "简历提取"}</span><p>{resultText(item.result)}</p>{item.evidence.map((evidence, index) => <p key={`${item.analysisVersionId}:evidence:${index}`}>证据：{evidenceText(evidence)}</p>)}{item.conflicts.map((value) => <p key={`${item.analysisVersionId}:conflict:${value}`}>冲突：{value}</p>)}{item.unknowns.map((unknown) => <p key={unknown}>未验证：{unknown}</p>)}{item.verificationQuestions.map((question) => <p key={question}>待验证：{question}</p>)}<small>上下文 {item.contextVersionId.slice(0, 8)} · 文档 {item.documentIds.length} · {item.agentVersion} · {item.modelVersion} · <time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString("zh-CN")}</time></small></article>;
  return <section aria-label="候选人" className="hr-r12-panel hr-candidate-workspace">
    <header><div><span>CANDIDATE INTELLIGENCE</span><h2>候选人</h2></div><div><strong>{relations.length} 位已确认</strong><button type="button" onClick={() => void refresh()}>刷新候选人状态</button></div></header>
    <section className="hr-candidate-import" aria-label="批量简历导入"><h3>批量上传简历</h3><p>每份简历独立解析；单份失败不会影响其他文件。</p><AttachmentUploader acceptedInputTypes={["pdf", "office", "text"]} client={uploadClient} conversationId={null} csrfToken={csrfToken} disabled={readOnly} limits={{ max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 100, max_bytes_per_message: 500 * 1024 * 1024, max_files_per_conversation: 100, max_bytes_per_conversation: 500 * 1024 * 1024 }} onQueueChange={setQueue} /><button disabled={readOnly || readyUploads === 0} type="button" onClick={() => void createBatch()}>开始解析 {readyUploads} 份简历</button></section>
    <section aria-label="简历解析状态"><h3>解析与确认</h3>{drafts.length === 0 && <p>尚未上传简历。</p>}{drafts.map((draft) => { const edit = edits[draft.draftId] ?? draftEdit(draft); const unknowns = Array.isArray(draft.extractedFacts.unknowns) ? draft.extractedFacts.unknowns.filter((item): item is string => typeof item === "string") : []; return <article key={draft.draftId} data-state={draft.state}><div><strong>{extractedName(draft)}</strong><span>{STATE_LABEL[draft.state]}</span></div><p>材料 {draft.attachmentId.slice(0, 8)}</p>{draft.state === "failed" && <><p>失败原因：{draft.errorCode ?? "未知错误"}</p><button disabled={readOnly} type="button" onClick={() => void retry(draft)}>重试解析</button></>}{draft.state === "ready" && <><button type="button" onClick={() => review(draft)}>审阅{extractedName(draft)}</button>{editingDraftId === draft.draftId && <form className="hr-candidate-confirm" onSubmit={(event) => { event.preventDefault(); void confirm(draft); }}><p>来源附件 {draft.attachmentId}</p>{unknowns.map((item) => <p key={item}>待人工核实：{item}</p>)}<label>候选人称谓<input aria-label="候选人称谓" disabled={readOnly} value={edit.stableName} onChange={(event) => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, stableName: event.target.value } }))} /></label><label>确认后的候选人事实<textarea aria-label="确认后的候选人事实 JSON" disabled={readOnly} value={edit.facts} onChange={(event) => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, facts: event.target.value } }))} /></label>{draft.identityCandidateIds.length > 0 && <fieldset disabled={readOnly}><legend>身份候选：必须明确选择</legend><label><input checked={edit.mergeCandidateId === null} name={`identity-${draft.draftId}`} type="radio" value="new" onChange={() => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, mergeCandidateId: null } }))} />新建候选人</label>{draft.identityCandidateIds.map((candidateId) => <label key={candidateId}><input checked={edit.mergeCandidateId === candidateId} name={`identity-${draft.draftId}`} type="radio" value={candidateId} onChange={() => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, mergeCandidateId: candidateId } }))} />合并到 {candidateId}</label>)}</fieldset>}<button disabled={readOnly || !currentContextVersionId || !edit.stableName.trim() || edit.mergeCandidateId === undefined} type="submit">确认候选人</button></form>}</>}</article>; })}</section>
    <section aria-label="已确认候选人"><h3>已确认候选人</h3>{relations.map((item) => { const comparable = item.relation.contextVersionId === currentContextVersionId; return <article key={item.relation.positionCandidateId}><label><input disabled={readOnly || !comparable} name="candidate-comparison" type="checkbox" checked={comparisonIds.includes(item.relation.positionCandidateId)} onChange={() => setComparisonIds((ids) => ids.includes(item.relation.positionCandidateId) ? ids.filter((id) => id !== item.relation.positionCandidateId) : [...ids, item.relation.positionCandidateId])} />加入比较</label><button type="button" onClick={() => void openCandidate(item)}>查看{item.candidate.stableName}</button><small>岗位上下文 {item.relation.contextVersionId.slice(0, 8)}{!comparable && " · 上下文版本不同，需重算后比较"}</small></article>; })}<button disabled={readOnly || comparisonIds.length < 2 || !currentContextVersionId} type="button" onClick={() => void compare()}>比较已选候选人</button></section>
    {comparison && <section aria-label="候选人比较结果" className="hr-candidate-comparison"><h3>候选人比较结果</h3>{renderAnalysis(comparison)}</section>}
    {selected && <section aria-label="候选人详情" className="hr-candidate-detail"><header><div><span>CANDIDATE</span><h3>{selected.candidate.stableName}</h3></div><button type="button" onClick={() => setSelected(null)}>关闭详情</button></header><pre>{JSON.stringify(selected.candidate.facts, null, 2)}</pre><p>{selected.documents.length} 份候选人材料</p><div className="hr-candidate-actions"><button disabled={readOnly || !currentContextVersionId || selected.relation.contextVersionId !== currentContextVersionId} type="button" onClick={() => void launch("candidate_match")}>生成匹配分析</button><button disabled={readOnly || !currentContextVersionId || selected.relation.contextVersionId !== currentContextVersionId} type="button" onClick={() => void launch("candidate_interview_plan")}>生成专属面试题</button><button type="button" onClick={() => void refreshAnalyses()}>刷新分析</button></div>{analysisTask && <p role="status">{TASK_LABEL[analysisTask.taskKind as keyof typeof TASK_LABEL]}：{taskStatus(analysisTask.status)}</p>}<section aria-label="分析历史"><h4>分析历史</h4>{orderedAnalyses.map(renderAnalysis)}</section>{newestAnalysis(orderedAnalyses) && <form onSubmit={(event) => { event.preventDefault(); void appendFeedback(); }}><label>人工纠正<textarea aria-label="人工纠正" disabled={readOnly} value={correction} onChange={(event) => setCorrection(event.target.value)} /></label><button disabled={readOnly || !correction.trim()} type="submit">记录人工纠正</button></form>}<section aria-label="人工反馈"><h4>人工反馈</h4>{selected.feedback.map((item) => <p key={item.feedbackId}>{item.correction ?? item.reason}</p>)}</section></section>}
    {!currentContextVersionId && <p role="status">确认岗位上下文后，才能确认候选人和生成岗位相对分析。</p>}
    {readOnly && <p role="status">当前为只读模式，不能上传、确认、分析或记录人工反馈。</p>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
