import { useEffect, useMemo, useRef, useState } from "react";
import { beginAttachmentUpload, completeAttachmentUpload, fetchConversationAttachment, issueAttachmentTicket, uploadAttachmentContent, type AttachmentUpload } from "../../attachmentApi";
import { MessageMarkdown } from "../../components/MessageMarkdown";
import { platformPath } from "../../auth";
import { createHrLoopApi, readPages, type ExactRef, type HrLoopApi, type SavedResult } from "../../hrLoopApi";
import { candidateError, createHrLoopCandidatesApi, type CandidateChoice, type CandidateView, type HrLoopCandidatesApi, type InterviewRecord, type InterviewRecordView } from "../../hrLoopCandidatesApi";
import "./HrLoopCandidatesPanel.css";

export type CandidateWorkSelection = { candidateId: string; displayName: string; positionId?: string; references: ExactRef[]; goal: string };
type PendingUpload = { text: string; title: string; positionId: string | null; key: string; file: File; upload?: AttachmentUpload; contentDone?: boolean; completeDone?: boolean; materialRef?: ExactRef };
const refKey = (ref: ExactRef) => `${ref.kind}:${ref.id}:${ref.revision}:${ref.sha256}`;

export function HrLoopCandidateWorkspace({ csrf, disabled, candidatesApi: injectedCandidates, api: injectedApi, onClose, onAccessError, onUse }: {
  csrf: string; disabled: boolean; candidatesApi?: HrLoopCandidatesApi; api?: HrLoopApi;
  onClose: () => void; onAccessError: (error: unknown) => void; onUse: (selection: CandidateWorkSelection) => void;
}) {
  const candidatesApi = useMemo(() => injectedCandidates ?? createHrLoopCandidatesApi(csrf), [injectedCandidates, csrf]);
  const api = useMemo(() => injectedApi ?? createHrLoopApi(csrf), [injectedApi, csrf]);
  const [choices, setChoices] = useState<CandidateChoice[]>([]), [candidateId, setCandidateId] = useState("");
  const [candidate, setCandidate] = useState<CandidateView | null>(null), [results, setResults] = useState<SavedResult[]>([]), [records, setRecords] = useState<InterviewRecord[]>([]);
  const [scopedResults, setScopedResults] = useState<SavedResult[]>([]), [originalLinks, setOriginalLinks] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<ExactRef[]>([]), [goal, setGoal] = useState(""), [positionId, setPositionId] = useState("");
  const [preview, setPreview] = useState<{ title: string; body: string } | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [rawText, setRawText] = useState(""), [rawTitle, setRawTitle] = useState(""), [pending, setPending] = useState<PendingUpload | null>(null), [saving, setSaving] = useState(false);
  const epoch = useRef(0), active = useRef(true);
  const current = (n: number) => active.current && epoch.current === n;
  const report = (e: unknown, n = epoch.current) => { if (!current(n)) return; setError(candidateError(e)); onAccessError(e); };
  useEffect(() => { active.current = true; void candidatesApi.candidates().then((v) => active.current && setChoices(v.items)).catch(report); return () => { active.current = false; epoch.current++; }; }, [candidatesApi]);
  useEffect(() => {
    const n = ++epoch.current;
    setCandidate(null); setResults([]); setScopedResults([]); setRecords([]); setSelected([]); setGoal(""); setPositionId(""); setPreview(null); setError(""); setNotice(""); setRawText(""); setRawTitle(""); setPending(null); setSaving(false); setOriginalLinks({});
    if (!candidateId) return;
    void Promise.all([
      candidatesApi.candidate(candidateId),
      readPages((cursor) => api.results({ candidate: candidateId, cursor }), () => current(n)),
      candidatesApi.interviewRecords(candidateId),
    ]).then(async ([view, listed, interview]) => {
      const listedResults = await Promise.all(listed.map((item) => api.result(item.ref)));
      const known = new Set(listedResults.map((item) => refKey(item.ref)));
      const confirmedResults = await Promise.all(view.documents.filter((d) => !known.has(refKey(d.result_ref))).map((d) => api.result(d.result_ref)));
      if (!current(n)) return;
      setCandidate(view); setPositionId(view.position_ids.length === 1 ? view.position_ids[0] : ""); setResults(listedResults); setScopedResults([...listedResults, ...confirmedResults]); setRecords(interview.items);
    }).catch((e) => report(e, n));
  }, [api, candidatesApi, candidateId]);
  const toggle = (ref: ExactRef) => setSelected((old) => old.some((r) => refKey(r) === refKey(ref)) ? old.filter((r) => refKey(r) !== refKey(ref)) : [...old, ref]);
  async function showResult(ref: ExactRef, title: string) { const n = epoch.current; try { const value = await api.result(ref); if (current(n)) setPreview({ title, body: value.body }); } catch (e) { report(e, n); } }
  async function showRecord(record: InterviewRecord) { const n = epoch.current; try { const value: InterviewRecordView = await candidatesApi.interviewRecord(candidateId, record.record_id); if (current(n)) setPreview({ title: value.title, body: value.text }); } catch (e) { report(e, n); } }
  async function openOriginal(attachmentId: string) { const n = epoch.current; try { const ticket = await issueAttachmentTicket(attachmentId, "download", csrf); if (current(n)) setOriginalLinks((old) => ({...old, [attachmentId]: platformPath(ticket.contentPath)})); } catch (e) { report(e, n); } }
  async function saveRaw() {
    if (!candidate || saving) return;
    let attempt = pending;
    if (!attempt || attempt.text !== rawText || attempt.title !== rawTitle.trim() || attempt.positionId !== (positionId || null)) {
      const text = rawText, title = rawTitle.trim();
      if (!text.trim() || Array.from(text).length > 32000 || !title || Array.from(title).length > 500) { setError("请填写标题和不超过 32000 字的面试原文。"); return; }
      attempt = { text, title, positionId: positionId || null, key: crypto.randomUUID(), file: new File([text], `${title}.txt`, { type: "text/plain;charset=utf-8" }) };
      setPending(attempt);
    }
    const n = epoch.current; setSaving(true); setError(""); setNotice("");
    try {
      attempt.upload ??= await beginAttachmentUpload(null, attempt.file, csrf);
      if (!attempt.materialRef) {
        if (!attempt.contentDone) {
          await uploadAttachmentContent(attempt.upload.uploadId, attempt.file, csrf);
          attempt.contentDone = true;
        }
        if (!attempt.completeDone) {
          await completeAttachmentUpload(attempt.upload.uploadId, csrf);
          attempt.completeDone = true;
        }
        let ready = await fetchConversationAttachment(attempt.upload.attachmentId);
        while (current(n) && !["ready", "quarantined", "rejected", "deleted"].includes(ready.state)) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          ready = await fetchConversationAttachment(attempt.upload.attachmentId);
        }
        if (ready.state !== "ready") throw new Error("attachment unavailable");
        const material = await api.material(attempt.upload.attachmentId);
        if (!material.text_ref) throw new Error("text unavailable");
        attempt.materialRef = material.text_ref;
      }
      const saved = await candidatesApi.registerInterviewRecord(candidate.candidate_id, { material_ref: attempt.materialRef, title: attempt.title, occurred_at: null, position_id: attempt.positionId, interview_plan_ref: null }, attempt.key);
      if (!current(n)) return;
      setRecords((old) => [...old, saved]); setPending(null); setRawText(""); setRawTitle(""); setNotice("原文已保存。");
    } catch (e) { if (current(n)) { setError("原文尚未保存，请重试登记。"); setPending(attempt); } } finally { if (current(n)) setSaving(false); }
  }
  const incompatible = scopedResults.filter((r) => selected.some((s) => refKey(s) === refKey(r.ref))).some((r) => {
    const candidates = r.objects.filter((o) => o.kind === "candidate").map((o) => o.id);
    const positions = r.objects.filter((o) => o.kind === "position").map((o) => o.id);
    return (candidates.length > 0 && (candidates.length !== 1 || candidates[0] !== candidateId)) || (positions.length > 0 && (positions.length !== 1 || positions[0] !== positionId));
  });
  return <section className="hr-loop-method hr-loop-candidate-workspace" aria-label="候选人工作">
    <header><h2>候选人工作</h2><button type="button" onClick={onClose}>关闭候选人工作</button></header>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <label>选择已确认候选人<select aria-label="选择候选人" value={candidateId} disabled={disabled} onChange={(e) => setCandidateId(e.target.value)}><option value="">请选择</option>{choices.filter((c) => c.available).map((c) => <option key={c.candidate_id} value={c.candidate_id}>{c.display_name ?? "已确认候选人"}</option>)}</select></label>
    {candidate && <>
      <p><strong>{candidate.display_name}</strong>　{candidate.summary}</p>
      {candidate.position_ids.length > 0 && <label>关联岗位<select aria-label="选择关联岗位" value={positionId} onChange={(e) => setPositionId(e.target.value)}><option value="">请选择关联岗位</option>{candidate.position_ids.map((id, i) => <option key={id} value={id}>{candidate.position_ids.length === 1 ? "关联岗位" : `关联岗位 ${i + 1}`}</option>)}</select></label>}
      <section aria-label="原始材料"><h3>原始材料</h3>{candidate.documents.map((d) => <div key={d.document_id}><label><input type="checkbox" aria-label={`选择准确正文 ${d.document_id}`} checked={selected.some((r) => refKey(r) === refKey(d.source_ref))} onChange={() => toggle(d.source_ref)}/> 用户原件提取的准确正文</label><button type="button" onClick={() => void openOriginal(d.attachment_id)}>获取原始材料 {d.document_id}</button>{originalLinks[d.attachment_id] && <a href={originalLinks[d.attachment_id]}>打开原件</a>}</div>)}</section>
      <section aria-label="已确认草稿"><h3>已确认草稿</h3>{candidate.documents.map((d) => <div key={d.document_id}><label><input type="checkbox" aria-label={`选择已确认草稿 ${d.result_ref.id}`} checked={selected.some((r) => refKey(r) === refKey(d.result_ref))} onChange={() => toggle(d.result_ref)}/> {d.summary ?? "人工确认的候选人材料草稿"}</label><button type="button" onClick={() => void showResult(d.result_ref, "已确认草稿")}>查看已确认草稿 {d.result_ref.id}</button></div>)}</section>
      <section aria-label="关联成果"><h3>关联成果</h3>{results.length === 0 ? <p>暂无关联成果。</p> : results.map((r) => <div key={refKey(r.ref)}><label><input type="checkbox" aria-label={`选择关联成果 ${r.ref.id}`} checked={selected.some((x) => refKey(x) === refKey(r.ref))} onChange={() => toggle(r.ref)}/> {r.kind === "interview_record" ? "AI 整理 · " : ""}{r.title}</label><button type="button" onClick={() => setPreview({title:r.title, body:r.body})}>查看关联成果 {r.ref.id}</button></div>)}</section>
      <section aria-label="面试记录"><h3>面试记录</h3>{records.map((r) => <div key={r.record_id}><label><input type="checkbox" aria-label={`选择面试记录 ${r.record_id}`} checked={selected.some((x) => refKey(x) === refKey(r.material_ref))} onChange={() => toggle(r.material_ref)}/> {r.authorship === "user_supplied" ? "用户提供" : "AI 整理"} · {r.title}</label><button type="button" onClick={() => void showRecord(r)}>查看面试记录 {r.record_id}</button></div>)}</section>
      <section aria-label="登记面试原文"><h3>登记面试原文</h3><p>此处保存用户提供的逐字原文，与 AI 整理内容分别标识。</p><label>原文标题<input aria-label="原文标题" value={rawTitle} disabled={disabled || saving} onChange={(e) => setRawTitle(e.target.value)}/></label><label>面试原文<textarea aria-label="面试原文" rows={6} value={rawText} disabled={disabled || saving} onChange={(e) => setRawText(e.target.value)}/></label><button type="button" disabled={disabled || saving || !rawTitle.trim() || !rawText.trim()} onClick={() => void saveRaw()}>{pending ? "重试登记" : "登记用户原文"}</button></section>
      {preview && <section aria-label="准确内容预览"><h3>{preview.title}</h3><MessageMarkdown content={preview.body}/></section>}
      <label>继续工作的意图<textarea aria-label="继续工作的意图" value={goal} rows={3} placeholder="例如：基于所选准确材料，整理下一轮需要核实的问题。" onChange={(e) => setGoal(e.target.value)}/></label>
      {incompatible && <p role="alert">所选成果与当前关联岗位不一致，请选择正确岗位或取消该成果。</p>}
      <button type="button" disabled={disabled || !goal.trim() || incompatible} onClick={() => onUse({candidateId:candidate.candidate_id, displayName:candidate.display_name, ...(positionId ? {positionId} : {}), references:selected, goal:goal.trim()})}>带所选内容继续工作</button>
    </>}
  </section>;
}
