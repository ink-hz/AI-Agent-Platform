import { useEffect, useRef, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type {
  HrCandidate, HrCandidateAnalysisVersion, HrCandidateDocument, HrCandidateDraft,
  HrHumanFeedback, HrPositionCandidate,
} from "../../hrR12Types";
import {
  AttachmentUploader, type AttachmentUploadClient, type UploadQueueItem,
} from "../../components/conversation/AttachmentUploader";

type CandidateApi = Pick<HrR12Api,
  "candidateDrafts" | "retryDraft" | "confirmDraft" | "createCandidateDraftBatch"
  | "positionCandidates" | "candidate" | "candidateDocuments" | "candidateAnalyses"
  | "candidateFeedback" | "appendCandidateFeedback" | "compareCandidates" | "startTask">;
type NamedRelation = { relation: HrPositionCandidate; candidate: HrCandidate };

const STATE_LABEL: Record<HrCandidateDraft["state"], string> = {
  pending: "等待解析", processing: "正在解析", ready: "待确认", failed: "解析失败",
  confirmed: "已确认", dismissed: "已忽略",
};

function extractedName(draft: HrCandidateDraft): string {
  const value = draft.extractedFacts.stable_name;
  return typeof value === "string" && value.trim() ? value.trim() : `候选人 ${draft.attachmentId.slice(0, 8)}`;
}
function resultText(value: Record<string, unknown>): string {
  const summary = value.summary;
  return typeof summary === "string" ? summary : JSON.stringify(value, null, 2);
}

export function HrCandidateWorkspace({ api, positionId, csrfToken, currentContextVersionId, uploadClient }: {
  api: CandidateApi; positionId: string; csrfToken: string;
  currentContextVersionId: string | null; uploadClient?: AttachmentUploadClient;
}) {
  const [drafts, setDrafts] = useState<HrCandidateDraft[]>([]);
  const [relations, setRelations] = useState<NamedRelation[]>([]);
  const [queue, setQueue] = useState<UploadQueueItem[]>([]);
  const [selected, setSelected] = useState<{ relation: HrPositionCandidate; candidate: HrCandidate; documents: HrCandidateDocument[]; analyses: HrCandidateAnalysisVersion[]; feedback: HrHumanFeedback[] } | null>(null);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [correction, setCorrection] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const mutation = useRef<AbortController | null>(null);

  async function load(signal?: AbortSignal) {
    const [nextDrafts, nextRelations] = await Promise.all([
      api.candidateDrafts(positionId, signal), api.positionCandidates(positionId, signal),
    ]);
    const named = await Promise.all(nextRelations.map(async (relation) => ({ relation, candidate: await api.candidate(relation.candidateId, signal) })));
    if (!signal?.aborted) { setDrafts(nextDrafts); setRelations(named); }
  }
  useEffect(() => {
    const controller = new AbortController(); setNotice(null); setSelected(null); setComparisonIds([]);
    void load(controller.signal).catch(() => { if (!controller.signal.aborted) setNotice("候选人数据暂时不可用"); });
    return () => { controller.abort(); mutation.current?.abort(); };
  }, [api, positionId]);

  function controller(): AbortController { mutation.current?.abort(); const next = new AbortController(); mutation.current = next; return next; }
  async function createBatch() {
    const attachmentIds = queue.filter((item) => item.state === "ready" && item.attachment).map((item) => item.attachment!.attachmentId);
    if (attachmentIds.length === 0) return;
    const current = controller(); setNotice("正在提交简历解析…");
    try { const batch = await api.createCandidateDraftBatch(positionId, attachmentIds, crypto.randomUUID(), current.signal); if (!current.signal.aborted) { setDrafts((items) => [...batch.items, ...items.filter((item) => !batch.items.some((added) => added.draftId === item.draftId))]); setNotice(`已提交 ${batch.items.length} 份简历，刷新页面仍可继续查看进度。`); } } catch { if (!current.signal.aborted) setNotice("简历批量解析未启动，可以安全重试。"); }
  }
  async function retry(draft: HrCandidateDraft) {
    const current = controller();
    try { const next = await api.retryDraft(draft.draftId, draft.rowVersion, crypto.randomUUID(), current.signal); if (!current.signal.aborted) setDrafts((items) => items.map((item) => item.draftId === draft.draftId ? next : item)); } catch { if (!current.signal.aborted) setNotice("重试解析未完成，请重试。"); }
  }
  async function confirm(draft: HrCandidateDraft) {
    if (!currentContextVersionId) { setNotice("请先确认岗位上下文，再确认候选人。"); return; }
    const current = controller();
    try {
      const created = await api.confirmDraft(draft.draftId, { expectedRowVersion: draft.rowVersion, contextVersionId: currentContextVersionId, stableName: extractedName(draft), confirmedFacts: draft.extractedFacts, mergeCandidateId: null }, crypto.randomUUID(), current.signal);
      if (!current.signal.aborted) { setDrafts((items) => items.map((item) => item.draftId === draft.draftId ? { ...item, state: "confirmed" } : item)); setRelations((items) => [...items.filter((item) => item.relation.positionCandidateId !== created.positionCandidate.positionCandidateId), { relation: created.positionCandidate, candidate: created.candidate }]); setNotice("候选人已确认，AI 提取事实与人工确认记录保持分离。"); }
    } catch { if (!current.signal.aborted) setNotice("候选人确认未完成，请核对身份冲突或重试。"); }
  }
  async function openCandidate(item: NamedRelation) {
    const current = controller(); setNotice(null);
    try {
      const [documents, analyses, feedback] = await Promise.all([api.candidateDocuments(item.candidate.candidateId, current.signal), api.candidateAnalyses(item.relation.positionCandidateId, current.signal), api.candidateFeedback(item.relation.positionCandidateId, current.signal)]);
      if (!current.signal.aborted) setSelected({ ...item, documents, analyses, feedback });
    } catch { if (!current.signal.aborted) setNotice("候选人详情暂时不可用"); }
  }
  async function launch(kind: "candidate_match" | "candidate_interview_plan") {
    if (!selected || !currentContextVersionId) return;
    const current = controller();
    try { await api.startTask(positionId, kind, crypto.randomUUID(), { contextVersionId: currentContextVersionId, candidate: { candidateId: selected.candidate.candidateId, positionCandidateId: selected.relation.positionCandidateId }, materialIds: [] }, current.signal); if (!current.signal.aborted) setNotice(kind === "candidate_match" ? "匹配分析已启动，任务会在刷新后继续显示。" : "候选人专属面试题已启动，任务会在刷新后继续显示。"); } catch { if (!current.signal.aborted) setNotice("候选人任务未启动，可以安全重试。"); }
  }
  async function appendFeedback() {
    const latest = selected?.analyses[0]; if (!selected || !latest || !correction.trim()) return;
    const current = controller();
    try { const saved = await api.appendCandidateFeedback(selected.relation.positionCandidateId, { analysisVersionId: latest.analysisVersionId, feedbackKind: "correction", conclusionKey: "overall", correction: correction.trim(), reason: "HR 人工核实" }, crypto.randomUUID(), current.signal); if (!current.signal.aborted) { setSelected((value) => value ? { ...value, feedback: [saved, ...value.feedback] } : value); setCorrection(""); setNotice("人工纠正已单独记录，后续分析会引用但不会改写旧 AI 版本。"); } } catch { if (!current.signal.aborted) setNotice("人工纠正未保存，请重试。"); }
  }
  async function compare() {
    if (!currentContextVersionId || comparisonIds.length < 2) return;
    const current = controller();
    try { const result = await api.compareCandidates(positionId, comparisonIds, currentContextVersionId, crypto.randomUUID(), current.signal); if (!current.signal.aborted) setNotice(`候选人比较已生成：分析版本 v${result.versionNumber}`); } catch (error) { if (!current.signal.aborted) setNotice((error as { status?: number }).status === 409 ? "候选人使用的岗位上下文版本不同，请重算后再比较。" : "候选人比较未完成，请重试。"); }
  }

  const readyUploads = queue.filter((item) => item.state === "ready" && item.attachment).length;
  return <section aria-label="候选人" className="hr-r12-panel hr-candidate-workspace">
    <header><div><span>CANDIDATE INTELLIGENCE</span><h2>候选人</h2></div><strong>{relations.length} 位已确认</strong></header>
    <section className="hr-candidate-import" aria-label="批量简历导入"><h3>批量上传简历</h3><p>每份简历独立解析；单份失败不会影响其他文件。</p><AttachmentUploader acceptedInputTypes={["pdf", "office", "text"]} client={uploadClient} conversationId={null} csrfToken={csrfToken} limits={{ max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 100, max_bytes_per_message: 500 * 1024 * 1024, max_files_per_conversation: 100, max_bytes_per_conversation: 500 * 1024 * 1024 }} onQueueChange={setQueue} /><button disabled={readyUploads === 0} type="button" onClick={() => void createBatch()}>开始解析 {readyUploads} 份简历</button></section>
    <section aria-label="简历解析状态"><h3>解析与确认</h3>{drafts.length === 0 && <p>尚未上传简历。</p>}{drafts.map((draft) => <article key={draft.draftId} data-state={draft.state}><div><strong>{extractedName(draft)}</strong><span>{STATE_LABEL[draft.state]}</span></div><p>材料 {draft.attachmentId.slice(0, 8)}</p>{draft.state === "failed" && <><p>失败原因：{draft.errorCode ?? "未知错误"}</p><button type="button" onClick={() => void retry(draft)}>重试解析</button></>}{draft.state === "ready" && <button disabled={!currentContextVersionId} type="button" onClick={() => void confirm(draft)}>确认候选人</button>}</article>)}</section>
    <section aria-label="已确认候选人"><h3>已确认候选人</h3>{relations.map((item) => <article key={item.relation.positionCandidateId}><label><input name="candidate-comparison" type="checkbox" checked={comparisonIds.includes(item.relation.positionCandidateId)} onChange={() => setComparisonIds((ids) => ids.includes(item.relation.positionCandidateId) ? ids.filter((id) => id !== item.relation.positionCandidateId) : [...ids, item.relation.positionCandidateId])} />加入比较</label><button type="button" onClick={() => void openCandidate(item)}>查看{item.candidate.stableName}</button><small>岗位上下文 {item.relation.contextVersionId.slice(0, 8)}</small></article>)}<button disabled={comparisonIds.length < 2 || !currentContextVersionId} type="button" onClick={() => void compare()}>比较已选候选人</button></section>
    {selected && <section aria-label="候选人详情" className="hr-candidate-detail"><header><div><span>CANDIDATE</span><h3>{selected.candidate.stableName}</h3></div><button type="button" onClick={() => setSelected(null)}>关闭详情</button></header><pre>{JSON.stringify(selected.candidate.facts, null, 2)}</pre><p>{selected.documents.length} 份候选人材料</p><div className="hr-candidate-actions"><button disabled={!currentContextVersionId} type="button" onClick={() => void launch("candidate_match")}>生成匹配分析</button><button disabled={!currentContextVersionId} type="button" onClick={() => void launch("candidate_interview_plan")}>生成专属面试题</button></div><section aria-label="分析历史"><h4>分析历史</h4>{selected.analyses.map((item) => <article key={item.analysisVersionId}><strong>分析版本 v{item.versionNumber}</strong><span>{item.analysisKind === "match" ? "岗位匹配" : item.analysisKind === "candidate_interview_plan" ? "专属面试题" : item.analysisKind === "comparison" ? "候选人比较" : "简历提取"}</span><p>{resultText(item.result)}</p>{item.unknowns.map((unknown) => <p key={unknown}>未验证：{unknown}</p>)}{item.verificationQuestions.map((question) => <p key={question}>待验证：{question}</p>)}</article>)}</section>{selected.analyses[0] && <form onSubmit={(event) => { event.preventDefault(); void appendFeedback(); }}><label>人工纠正<textarea value={correction} onChange={(event) => setCorrection(event.target.value)} /></label><button disabled={!correction.trim()} type="submit">记录人工纠正</button></form>}<section aria-label="人工反馈"><h4>人工反馈</h4>{selected.feedback.map((item) => <p key={item.feedbackId}>{item.correction ?? item.reason}</p>)}</section></section>}
    {!currentContextVersionId && <p role="status">确认岗位上下文后，才能确认候选人和生成岗位相对分析。</p>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
