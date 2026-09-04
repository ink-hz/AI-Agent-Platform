import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { platformPath } from "../../auth";
import type { HrR12Api } from "../../hrR12Api";
import type { ConversationFeedbackRating, ConversationFeedbackReason } from "../../conversationTypes";
import type {
  HrCandidate, HrCandidateAnalysisVersion, HrCandidateDocument, HrCandidateDraft,
  HrHumanFeedback, HrPositionCandidate, HrTaskRecord,
} from "../../hrR12Types";
import {
  AttachmentUploader, type AttachmentUploadClient, type UploadQueueItem,
} from "../../components/conversation/AttachmentUploader";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";
import { HrCandidateAnalysisCard } from "./HrCandidateAnalysisCard";

type CandidateApi = Pick<HrR12Api,
  "candidateDrafts" | "retryDraft" | "confirmDraft" | "createCandidateDraftBatch"
  | "positionCandidates" | "candidate" | "candidateDocuments" | "candidateAnalyses"
  | "candidateFeedback" | "appendCandidateFeedback" | "compareCandidates"
  | "downloadCandidateDocument" | "resources" | "downloadResource"
  | "startTask" | "taskStatus">;
type NamedRelation = { relation: HrPositionCandidate; candidate: HrCandidate };
type CandidateDetail = NamedRelation & { documents: HrCandidateDocument[]; analyses: HrCandidateAnalysisVersion[]; feedback: HrHumanFeedback[] };
type DraftEdit = { stableName: string; facts: string; mergeCandidateId: string | null | undefined };

const STATE_LABEL: Record<HrCandidateDraft["state"], string> = {
  pending: "等待解析", processing: "正在解析", ready: "待确认", failed: "解析失败",
  confirmed: "已确认", dismissed: "已忽略",
};
const TASK_LABEL = { candidate_match: "匹配分析", candidate_interview_plan: "候选人专属面试题" } as const;
const FEEDBACK_REASON_LABEL: Record<ConversationFeedbackReason, string> = {
  inaccurate: "信息不准确", incomplete: "信息不完整", unclear: "表达不清楚",
  unresolved: "没有解决问题", file_format: "文件或格式有问题",
  source_timeliness: "来源或时效有问题", other: "其他",
};
const MAX_DRAFT_POLL_ATTEMPTS = 6;

function extractedName(draft: HrCandidateDraft): string {
  const value = draft.extractedFacts.stable_name;
  return typeof value === "string" && value.trim() ? value.trim() : `候选人 ${draft.attachmentId.slice(0, 8)}`;
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
function factLabel(value: string): string {
  const labels: Record<string, string> = { skills: "技能", experience: "经历", years: "年限", education: "教育背景", location: "所在地" };
  return labels[value] ?? value.replace(/_/g, " ");
}
function factValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(factValue).filter(Boolean).join("、") || "未提供";
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${factLabel(key)}：${factValue(item)}`).join("；") || "未提供";
  return "未提供";
}
function CandidateFacts({ facts }: { facts: Record<string, unknown> }) {
  const entries = Object.entries(facts);
  return <section aria-label="候选人事实" className="hr-candidate-facts"><h4>候选人事实</h4>{entries.length === 0
    ? <p>暂无已确认事实</p>
    : <dl>{entries.map(([key, value]) => <div key={key}><dt>{factLabel(key)}</dt><dd>{factValue(value)}</dd></div>)}</dl>}</section>;
}

const LEGACY_MAX_DEPTH = 4;
const LEGACY_MAX_ITEMS = 50;
const LEGACY_MAX_TEXT = 500;
const LEGACY_PROTECTED_KEY_SEPARATORS = /[\s\u001c-\u001f\u0085_.:/-]+/gu;
// Mirrors backend/app/hr/candidate_models.py `_normalized_fact_key` and
// `_FORBIDDEN_FACT_KEYS`; legacy rendering remains defense-in-depth for old rows.
const LEGACY_PROTECTED_KEYS = new Set([
  "age", "birthdate", "dateofbirth", "disability", "ethnicity", "gender", "health",
  "maritalstatus", "nationality", "onboarding", "offerstatus", "pipelinestage",
  "politicalaffiliation", "pregnancy", "race", "religion", "sexualorientation",
  "storagekey", "storagepath", "objectkey", "objectref", "objectrefciphertext",
  "immutablelocator", "ats", "atsid", "interviewschedule", "automaticrejection",
  "beisen", "bosszhipin", "liepin", "年龄", "出生日期", "生日", "残疾", "残障",
  "民族", "性别", "健康", "健康状况", "婚姻", "婚姻状况", "婚育", "国籍", "入职",
  "录用状态", "流程阶段", "政治面貌", "怀孕", "孕期", "种族", "宗教", "性取向",
  "存储键", "存储路径", "对象键", "对象引用", "不可变定位符", "面试安排", "自动淘汰",
]);
const LEGACY_FIELD_LABELS: Record<string, string> = {
  stable_name: "候选人姓名", summary: "摘要", contact: "联系方式", education: "教育背景",
  experiences: "经历", projects: "项目", skills: "技能", certifications: "证书",
  languages: "语言", awards: "奖项", publications: "公开发表", unknowns: "待验证信息",
  sources: "来源", company: "公司", role: "角色", achievements: "成果", name: "名称",
  details: "详情", responsibility: "职责", availability: "可工作地点", unknown_list: "补充信息",
  additional_facts: "其他事实", additional_context: "其他比较信息", delivery_difference: "交付差异",
  position_candidate_id: "岗位候选关系", candidate_id: "候选人 ID", evidence_coverage: "证据覆盖",
  unknown_count: "待验证项", comparison_basis: "比较基准", ranking: "候选排序",
};

function legacyRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function legacyLabel(value: string): string {
  const knownLabel = Object.prototype.hasOwnProperty.call(LEGACY_FIELD_LABELS, value)
    ? LEGACY_FIELD_LABELS[value]
    : undefined;
  return boundedText(knownLabel ?? value.replace(/_/g, " "));
}
function boundedText(value: string): string {
  const characters = Array.from(value);
  return characters.length <= LEGACY_MAX_TEXT ? value : `${characters.slice(0, LEGACY_MAX_TEXT).join("")}…`;
}
function normalizedLegacyKey(value: string): string {
  // After NFKC, ß/ẞ -> ss is the only Python casefold expansion that can
  // produce the ASCII-only protected keys; toLowerCase handles the rest.
  const casefolded = value.normalize("NFKC").trim().toLowerCase().replace(/ß/gu, "ss");
  return casefolded.replace(LEGACY_PROTECTED_KEY_SEPARATORS, "");
}
function protectedLegacyField(value: string): boolean {
  return LEGACY_PROTECTED_KEYS.has(normalizedLegacyKey(value));
}
function LegacyValue({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (value === null || value === undefined) return <span>未提供</span>;
  if (typeof value === "string") return <span>{boundedText(value)}</span>;
  if (typeof value === "number" || typeof value === "boolean") return <span>{String(value)}</span>;
  if (depth >= LEGACY_MAX_DEPTH) return <span>内容层级过深，未展开</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span>暂无</span>;
    const visible = value.slice(0, LEGACY_MAX_ITEMS);
    return <ul className="hr-candidate-legacy-list">{visible.map((item, index) => <li key={index}><LegacyValue depth={depth + 1} value={item} /></li>)}{value.length > visible.length && <li>其余 {value.length - visible.length} 项未展开</li>}</ul>;
  }
  if (legacyRecord(value)) {
    const safeEntries = Object.entries(value).filter(([key]) => !protectedLegacyField(key));
    const entries = safeEntries.slice(0, LEGACY_MAX_ITEMS);
    if (entries.length === 0) return <span>暂无</span>;
    return <dl className="hr-candidate-legacy-fields">{entries.map(([key, item]) => <div key={key}><dt>{legacyLabel(key)}</dt><dd><LegacyValue depth={depth + 1} value={item} /></dd></div>)}{safeEntries.length > entries.length && <div><dt>更多信息</dt><dd>其余 {safeEntries.length - entries.length} 项未展开</dd></div>}</dl>;
  }
  return <span>未提供</span>;
}

function ComparisonResult({ candidateNames, result }: { candidateNames: Map<string, string>; result: Record<string, unknown> }) {
  const candidates = Array.isArray(result.candidates) ? result.candidates.filter(legacyRecord).slice(0, LEGACY_MAX_ITEMS) : [];
  const comparisonBasis = result.comparison_basis === "same_position_context" ? "同一岗位上下文" : result.comparison_basis;
  const remaining = Object.fromEntries(Object.entries(result).filter(([key]) => !["candidates", "ranking", "comparison_basis"].includes(key)));
  return <div className="hr-candidate-legacy-result hr-candidate-comparison-result">
    <section><h5>比较基准</h5><LegacyValue value={comparisonBasis} /></section>
    <section><h5>候选人对比</h5>{candidates.length === 0 ? <p>暂无候选人比较明细</p> : <ol>{candidates.map((candidate, index) => {
      const candidateId = typeof candidate.candidate_id === "string" ? candidate.candidate_id : null;
      const relationId = typeof candidate.position_candidate_id === "string" ? candidate.position_candidate_id : null;
      const name = candidateId ? candidateNames.get(candidateId) : null;
      const candidateRemaining = Object.fromEntries(Object.entries(candidate).filter(([key]) => !["position_candidate_id", "candidate_id", "summary", "evidence_coverage", "unknown_count"].includes(key)));
      return <li key={relationId ?? candidateId ?? index}><h6>{name ?? candidateId ?? relationId ?? `候选人 ${index + 1}`}</h6>{candidateId && name && <small>候选人 ID：{candidateId}</small>}<p>{typeof candidate.summary === "string" ? boundedText(candidate.summary) : "暂无比较摘要"}</p><dl><div><dt>证据覆盖</dt><dd>{typeof candidate.evidence_coverage === "number" ? `${candidate.evidence_coverage} 条` : "未提供"}</dd></div><div><dt>待验证项</dt><dd>{typeof candidate.unknown_count === "number" ? `${candidate.unknown_count} 项` : "未提供"}</dd></div></dl>{Object.keys(candidateRemaining).length > 0 && <LegacyValue value={candidateRemaining} />}</li>;
    })}</ol>}</section>
    <section><h5>候选排序</h5>{result.ranking === null || result.ranking === undefined ? <p>未提供单一排序</p> : <LegacyValue value={result.ranking} />}</section>
    {Object.keys(remaining).length > 0 && <section><h5>其他比较信息</h5><LegacyValue value={remaining} /></section>}
  </div>;
}

function ResumeExtractResult({ result }: { result: Record<string, unknown> }) {
  const facts = legacyRecord(result.extracted_facts) ? result.extracted_facts : result;
  const identities = Array.isArray(result.identity_candidate_ids) ? result.identity_candidate_ids : [];
  const remaining = legacyRecord(result.extracted_facts)
    ? Object.fromEntries(Object.entries(result).filter(([key]) => !["extracted_facts", "identity_candidate_ids"].includes(key)))
    : {};
  return <div className="hr-candidate-legacy-result hr-candidate-resume-extract-result">
    <section><h5>候选人身份</h5><p>{typeof facts.stable_name === "string" ? boundedText(facts.stable_name) : "姓名待确认"}</p>{identities.length > 0 && <><h6>可能关联的候选人</h6><LegacyValue value={identities} /></>}</section>
    <section><h5>提取事实</h5><LegacyValue value={Object.fromEntries(Object.entries(facts).filter(([key]) => key !== "stable_name"))} /></section>
    {Object.keys(remaining).length > 0 && <section><h5>其他提取信息</h5><LegacyValue value={remaining} /></section>}
  </div>;
}

function LegacyAnalysisCard({ analysis, candidateNames }: { analysis: Extract<HrCandidateAnalysisVersion, { analysisKind: "resume_extract" | "comparison" }>; candidateNames: Map<string, string> }) {
  return <article aria-label={analysis.analysisKind === "comparison" ? "候选人比较" : "简历提取"} className="hr-candidate-legacy-analysis-card">
    <header><strong>分析版本 v{analysis.versionNumber}</strong><span>{analysis.analysisKind === "comparison" ? "候选人比较" : "简历提取"}</span></header>
    {analysis.analysisKind === "comparison" ? <ComparisonResult candidateNames={candidateNames} result={analysis.result} /> : <ResumeExtractResult result={analysis.result} />}
    {analysis.conflicts.length > 0 && <section><h5>冲突信息</h5>{analysis.conflicts.map((value) => <p key={value}>冲突：{value}</p>)}</section>}
    <small>岗位上下文 {analysis.contextVersionId} · 文档版本 {analysis.documentIds.join("、")} · {analysis.agentVersion} · {analysis.modelVersion}</small>
  </article>;
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
  const [analysisFeedback, setAnalysisFeedback] = useState<Record<string, ConversationFeedbackRating | "pending" | "error">>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [analysisTask, setAnalysisTask] = useState<HrTaskRecord | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const analysisTaskScope = useRef<{ positionCandidateId: string; candidateId: string; taskKind: keyof typeof TASK_LABEL } | null>(null);
  const selectedRelationId = useRef<string | null>(null);
  const mutation = useRef<AbortController | null>(null);
  const draftPollAttempt = useRef(0);

  async function load(signal?: AbortSignal) {
    const [nextDrafts, nextRelations] = await Promise.all([api.candidateDrafts(positionId, signal), api.positionCandidates(positionId, signal)]);
    const named = await Promise.all(nextRelations.map(async (relation) => ({ relation, candidate: await api.candidate(relation.candidateId, signal) })));
    if (!signal?.aborted) { setDrafts(nextDrafts); setRelations(named); setLoadState("ready"); }
  }
  useEffect(() => {
    draftPollAttempt.current = 0;
    const controller = new AbortController(); setNotice(null); setSelected(null); setComparisonIds([]); setComparison(null); setLoadState("loading");
    void load(controller.signal).catch(() => { if (!controller.signal.aborted) { setLoadState("error"); setNotice("候选人数据暂时无法读取"); } });
    return () => { controller.abort(); mutation.current?.abort(); };
  }, [api, positionId, loadAttempt]);

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
    const scope = analysisTaskScope.current;
    if (!analysisTask || !scope || !["accepted", "running"].includes(analysisTask.status)) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      void api.taskStatus(positionId, analysisTask.taskId, controller.signal).then(async (terminal) => {
        if (controller.signal.aborted) return;
        if (terminal.positionCandidateId !== scope.positionCandidateId || terminal.candidateId !== scope.candidateId || terminal.taskKind !== scope.taskKind) {
          setAnalysisTask(null); setNotice("候选人任务绑定异常，已停止自动刷新。"); return;
        }
        setAnalysisTask(terminal);
        if (terminal.status === "accepted" || terminal.status === "running") return;
        if (terminal.status === "failed") { if (selectedRelationId.current === scope.positionCandidateId) setNotice(`${TASK_LABEL[terminal.taskKind as keyof typeof TASK_LABEL]}执行失败：${terminal.error ?? "未知错误"}`); return; }
        const analyses = await api.candidateAnalyses(scope.positionCandidateId, controller.signal);
        if (!controller.signal.aborted) { setSelected((value) => value?.relation.positionCandidateId === scope.positionCandidateId ? { ...value, analyses } : value); if (selectedRelationId.current === scope.positionCandidateId) setNotice(`${TASK_LABEL[scope.taskKind]}已完成，分析版本已刷新。`); }
      }).catch(() => { if (!controller.signal.aborted) setNotice("候选人任务状态暂时无法刷新，可手动刷新分析。"); });
    }, 1_000);
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [analysisTask, api, positionId]);

  function controller(): AbortController { mutation.current?.abort(); const next = new AbortController(); mutation.current = next; return next; }
  async function refresh() { draftPollAttempt.current = 0; const current = controller(); try { await load(current.signal); if (!current.signal.aborted) setNotice("候选人状态已刷新。"); } catch { if (!current.signal.aborted) setNotice("候选人状态暂时无法刷新。"); } }
  async function createBatch() {
    if (readOnly) return;
    const attachmentIds = queue.filter((item) => item.state === "ready" && item.attachment).map((item) => item.attachment!.attachmentId);
    if (attachmentIds.length === 0) return;
    const current = controller(); setNotice("正在提交简历解析…");
    const operation = retainMutationRequest(`candidate-batch:${positionId}`, attachmentIds);
    try { const batch = await api.createCandidateDraftBatch(positionId, attachmentIds, operation.requestId, current.signal); if (!current.signal.aborted) { completeMutationRequest(operation.key); draftPollAttempt.current = 0; setDrafts((items) => [...batch.items, ...items.filter((item) => !batch.items.some((added) => added.draftId === item.draftId))]); setNotice(`已提交 ${batch.items.length} 份简历，正在自动刷新解析状态。`); } } catch { if (!current.signal.aborted) setNotice("简历批量解析未启动，可以安全重试。"); }
  }
  async function retry(draft: HrCandidateDraft) {
    if (readOnly) return;
    const current = controller();
    const operation = retainMutationRequest(`candidate-retry:${draft.draftId}`, { rowVersion: draft.rowVersion });
    try { const next = await api.retryDraft(draft.draftId, draft.rowVersion, operation.requestId, current.signal); if (!current.signal.aborted) { completeMutationRequest(operation.key); draftPollAttempt.current = 0; setDrafts((items) => items.map((item) => item.draftId === draft.draftId ? next : item)); } } catch { if (!current.signal.aborted) setNotice("重试解析未完成，请重试。"); }
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
    const input = { expectedRowVersion: draft.rowVersion, contextVersionId: currentContextVersionId, stableName: edit.stableName.trim(), confirmedFacts, mergeCandidateId: edit.mergeCandidateId };
    const operation = retainMutationRequest(`candidate-confirm:${draft.draftId}`, input);
    try {
      const created = await api.confirmDraft(draft.draftId, input, operation.requestId, current.signal);
      if (!current.signal.aborted) { completeMutationRequest(operation.key); setDrafts((items) => items.map((item) => item.draftId === draft.draftId ? { ...item, state: "confirmed" } : item)); setRelations((items) => [...items.filter((item) => item.relation.positionCandidateId !== created.positionCandidate.positionCandidateId), { relation: created.positionCandidate, candidate: created.candidate }]); setEditingDraftId(null); setNotice("候选人已确认，AI 提取事实与人工确认记录保持分离。"); }
    } catch (error) {
      if (current.signal.aborted) return;
      if ((error as { status?: number }).status === 409) { completeMutationRequest(operation.key); setNotice("身份或版本已变化，已保留编辑内容；刷新状态后可重试。"); try { const fresh = await api.candidateDrafts(positionId, current.signal); if (!current.signal.aborted) setDrafts(fresh); } catch { /* retained edit remains available */ } }
      else setNotice("候选人确认未完成，请核对输入后重试。");
    }
  }
  async function openCandidate(item: NamedRelation) {
    selectedRelationId.current = item.relation.positionCandidateId;
    const current = controller(); setNotice(analysisTask?.status === "failed" && analysisTaskScope.current?.positionCandidateId === item.relation.positionCandidateId ? `${TASK_LABEL[analysisTask.taskKind as keyof typeof TASK_LABEL]}执行失败：${analysisTask.error ?? "未知错误"}` : null); setCorrection("");
    try { const [documents, analyses, feedback] = await Promise.all([api.candidateDocuments(item.candidate.candidateId, current.signal), api.candidateAnalyses(item.relation.positionCandidateId, current.signal), api.candidateFeedback(item.relation.positionCandidateId, current.signal)]); if (!current.signal.aborted) setSelected({ ...item, documents, analyses, feedback }); }
    catch { if (!current.signal.aborted) setNotice("候选人详情暂时不可用"); }
  }
  async function refreshAnalyses() {
    if (!selected) return; const current = controller();
    try { const analyses = await api.candidateAnalyses(selected.relation.positionCandidateId, current.signal); if (!current.signal.aborted) { setSelected((value) => value ? { ...value, analyses } : value); setNotice("分析版本已刷新。"); } } catch { if (!current.signal.aborted) setNotice("分析版本暂时无法刷新。"); }
  }
  function preopen(): Window | null {
    const target = window.open("about:blank", "_blank");
    if (target) target.opener = null;
    return target;
  }
  async function openDocument(
    document: HrCandidateDocument,
    purpose: "preview" | "download",
  ) {
    if (readOnly || document.status !== "active") return;
    const target = preopen();
    if (!target) {
      setNotice("浏览器阻止了新窗口，请允许弹出窗口后重试。");
      return;
    }
    const current = controller();
    const input = { purpose };
    const operation = retainMutationRequest(
      `candidate-document-ticket:${document.documentId}:${purpose}`,
      input,
    );
    try {
      const issued = await api.downloadCandidateDocument(
        document.documentId,
        operation.requestId,
        purpose,
        current.signal,
      );
      if (current.signal.aborted) {
        target.close();
        return;
      }
      completeMutationRequest(operation.key);
      target.location.replace(platformPath(issued.contentPath));
    } catch {
      target.close();
      if (!current.signal.aborted) {
        setNotice(
          purpose === "preview"
            ? "简历预览未完成，请重试。"
            : "简历下载未完成，请重试。",
        );
      }
    }
  }
  async function downloadAnalysisPdf(item: HrCandidateAnalysisVersion) {
    if (readOnly || item.analysisKind !== "candidate_interview_plan" || !item.sourceArtifactVersionId) return;
    const target = preopen();
    if (!target) { setNotice("浏览器阻止了新窗口，请允许弹出窗口后重试。"); throw new Error("popup blocked"); }
    const current = controller();
    try {
      const resources = await api.resources(positionId, current.signal);
      const artifact = resources.artifacts.find((value) => value.artifactVersionId === item.sourceArtifactVersionId && value.mediaType === "application/pdf" && value.downloadAvailable);
      if (!artifact) {
        target.close();
        if (!current.signal.aborted) setNotice("PDF 尚未生成，重试本任务");
        throw new Error("candidate interview PDF unavailable");
      }
      const issued = await api.downloadResource(positionId, artifact.attachmentId, crypto.randomUUID(), "download", current.signal);
      if (current.signal.aborted) { target.close(); throw new Error("download aborted"); }
      target.location.replace(platformPath(issued.contentPath));
    } catch (error) {
      target.close();
      if (!current.signal.aborted && (error as Error).message !== "candidate interview PDF unavailable") setNotice("PDF 下载未完成，请重试本任务");
      throw error;
    }
  }
  async function launch(kind: "candidate_match" | "candidate_interview_plan") {
    if (readOnly || !selected || !currentContextVersionId || selected.relation.contextVersionId !== currentContextVersionId) return;
    const current = controller();
    const input = { contextVersionId: currentContextVersionId, candidate: { candidateId: selected.candidate.candidateId, positionCandidateId: selected.relation.positionCandidateId }, materialIds: [] };
    const operation = retainMutationRequest(`candidate-task:${positionId}:${kind}`, input);
    try {
      const task = await api.startTask(positionId, kind, operation.requestId, input, current.signal);
      if (current.signal.aborted) return;
      completeMutationRequest(operation.key);
      const scope = { positionCandidateId: selected.relation.positionCandidateId, candidateId: selected.candidate.candidateId, taskKind: kind };
      analysisTaskScope.current = scope;
      if (task.taskKind !== kind) { setAnalysisTask(null); setNotice("候选人任务绑定异常，已停止自动刷新。"); return; }
      setAnalysisTask(task);
      if (task.status === "failed") { setNotice(`${TASK_LABEL[kind]}执行失败：${task.error ?? "未知错误"}`); return; }
      if (task.status === "completed") {
        try {
          const analyses = await api.candidateAnalyses(scope.positionCandidateId, current.signal);
          if (!current.signal.aborted) { setSelected((value) => value?.relation.positionCandidateId === scope.positionCandidateId ? { ...value, analyses } : value); setNotice(`${TASK_LABEL[kind]}已完成，分析版本已刷新。`); }
        } catch { if (!current.signal.aborted) setNotice(`${TASK_LABEL[kind]}任务已完成，分析暂时无法刷新，请手动刷新。`); }
        return;
      }
      setNotice(`${TASK_LABEL[kind]}已启动，完成后将自动刷新分析版本。`);
    } catch { if (!current.signal.aborted) setNotice("候选人任务未启动，可以安全重试。"); }
  }
  async function appendFeedback() {
    if (readOnly) return;
    const latest = selected ? newestAnalysis(selected.analyses) : null; if (!selected || !latest || !correction.trim()) return;
    const current = controller();
    const input = { analysisVersionId: latest.analysisVersionId, feedbackKind: "correction" as const, conclusionKey: "overall", correction: correction.trim(), reason: "HR 人工核实" };
    const operation = retainMutationRequest(`candidate-feedback:${selected.relation.positionCandidateId}`, input);
    try { const saved = await api.appendCandidateFeedback(selected.relation.positionCandidateId, input, operation.requestId, current.signal); if (!current.signal.aborted) { completeMutationRequest(operation.key); setSelected((value) => value ? { ...value, feedback: [saved, ...value.feedback] } : value); setCorrection(""); setNotice("人工纠正已单独记录，后续分析会引用但不会改写旧 AI 版本。"); } } catch { if (!current.signal.aborted) setNotice("人工纠正未保存，请重试。"); }
  }
  async function recordAnalysisFeedback(item: HrCandidateAnalysisVersion, rating: ConversationFeedbackRating, reason: ConversationFeedbackReason | null, comment: string | null) {
    if (readOnly || !selected) return;
    setAnalysisFeedback((current) => ({ ...current, [item.analysisVersionId]: "pending" }));
    const input = {
      analysisVersionId: item.analysisVersionId,
      feedbackKind: rating === "helpful" ? "accepted" as const : "rejected" as const,
      conclusionKey: "overall", correction: null,
      reason: rating === "helpful" ? "HR 认可" : [reason ? FEEDBACK_REASON_LABEL[reason] : "需要改进", comment].filter(Boolean).join("："),
    };
    const current = controller();
    const operation = retainMutationRequest(`candidate-analysis-feedback:${selected.relation.positionCandidateId}:${item.analysisVersionId}`, input);
    try {
      const saved = await api.appendCandidateFeedback(selected.relation.positionCandidateId, input, operation.requestId, current.signal);
      if (!current.signal.aborted) {
        completeMutationRequest(operation.key);
        setSelected((value) => value ? { ...value, feedback: [saved, ...value.feedback] } : value);
        setAnalysisFeedback((value) => ({ ...value, [item.analysisVersionId]: rating }));
        setNotice("已记录对该分析版本的反馈。");
      }
    } catch { if (!current.signal.aborted) { setAnalysisFeedback((value) => ({ ...value, [item.analysisVersionId]: "error" })); setNotice("分析反馈未保存，请重试。"); } }
  }
  async function compare() {
    if (readOnly || !currentContextVersionId || comparisonIds.length < 2) return;
    const current = controller();
    const operation = retainMutationRequest(`candidate-comparison:${positionId}`, { comparisonIds, currentContextVersionId });
    try { const result = await api.compareCandidates(positionId, comparisonIds, currentContextVersionId, operation.requestId, current.signal); if (!current.signal.aborted) { completeMutationRequest(operation.key); setComparison(result); setNotice(`候选人比较已生成：分析版本 v${result.versionNumber}`); } } catch (error) { if (!current.signal.aborted) { if ((error as { status?: number }).status === 409) completeMutationRequest(operation.key); setNotice((error as { status?: number }).status === 409 ? "已选候选人的上下文版本已变化，请刷新后重算。" : "候选人比较未完成，请重试。"); } }
  }

  const readyUploads = queue.filter((item) => item.state === "ready" && item.attachment).length;
  const orderedAnalyses = useMemo(() => [...(selected?.analyses ?? [])].sort((left, right) => right.versionNumber - left.versionNumber), [selected?.analyses]);
  const candidateNames = useMemo(() => new Map(relations.map((item) => [item.candidate.candidateId, item.candidate.stableName])), [relations]);
  const renderAnalysis = (item: HrCandidateAnalysisVersion) => {
    const canRetry = !readOnly && Boolean(currentContextVersionId) && selected?.relation.contextVersionId === currentContextVersionId;
    const retryUnavailableReason = readOnly ? undefined : !currentContextVersionId
      ? "确认岗位上下文后才能重新生成此分析"
      : selected?.relation.contextVersionId !== currentContextVersionId
        ? "候选人的岗位上下文已变化，刷新后再重新生成"
        : undefined;
    return item.analysisKind === "match" || item.analysisKind === "candidate_interview_plan"
      ? <HrCandidateAnalysisCard
      analysis={item} feedbackState={analysisFeedback[item.analysisVersionId]}
      key={item.analysisVersionId} readOnly={readOnly}
      onDownload={item.analysisKind === "candidate_interview_plan" && item.sourceArtifactVersionId ? () => downloadAnalysisPdf(item) : undefined}
      onFeedback={(rating, reason, comment) => void recordAnalysisFeedback(item, rating, reason, comment)}
      onRetry={canRetry ? () => void launch(item.analysisKind === "match" ? "candidate_match" : "candidate_interview_plan") : undefined}
      retryUnavailableReason={retryUnavailableReason}
      />
      : <LegacyAnalysisCard analysis={item} candidateNames={candidateNames} key={item.analysisVersionId} />;
  };
  return <section aria-label="候选人" className="hr-r12-panel hr-candidate-workspace">
    <header><div><span>CANDIDATE INTELLIGENCE</span><h2>候选人</h2></div><div><strong>{relations.length} 位已确认</strong><button type="button" onClick={() => void refresh()}>刷新候选人状态</button></div></header>
    <section className="hr-candidate-import" aria-label="批量简历导入"><h3>批量上传简历</h3><p>每份简历独立解析；单份失败不会影响其他文件。</p><AttachmentUploader acceptedInputTypes={["pdf", "office", "text"]} client={uploadClient} conversationId={null} csrfToken={csrfToken} disabled={readOnly} limits={{ max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 100, max_bytes_per_message: 500 * 1024 * 1024, max_files_per_conversation: 100, max_bytes_per_conversation: 500 * 1024 * 1024 }} onQueueChange={setQueue} /><button disabled={readOnly || readyUploads === 0} type="button" onClick={() => void createBatch()}>开始解析 {readyUploads} 份简历</button></section>
    <section aria-label="简历解析状态"><h3>解析与确认</h3>{drafts.length === 0 && <p>尚未上传简历。</p>}{drafts.map((draft) => { const edit = edits[draft.draftId] ?? draftEdit(draft); const unknowns = Array.isArray(draft.extractedFacts.unknowns) ? draft.extractedFacts.unknowns.filter((item): item is string => typeof item === "string") : []; return <article key={draft.draftId} data-state={draft.state}><div><strong>{extractedName(draft)}</strong><span>{STATE_LABEL[draft.state]}</span></div><p>材料 {draft.attachmentId.slice(0, 8)}</p>{draft.state === "failed" && <><p>失败原因：{draft.errorCode ?? "未知错误"}</p><button disabled={readOnly} type="button" onClick={() => void retry(draft)}>重试解析</button></>}{draft.state === "ready" && <><button type="button" onClick={() => review(draft)}>审阅{extractedName(draft)}</button>{editingDraftId === draft.draftId && <form className="hr-candidate-confirm" onSubmit={(event) => { event.preventDefault(); void confirm(draft); }}><p>来源附件 {draft.attachmentId}</p>{unknowns.map((item) => <p key={item}>待人工核实：{item}</p>)}<label>候选人称谓<input aria-label="候选人称谓" disabled={readOnly} value={edit.stableName} onChange={(event) => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, stableName: event.target.value } }))} /></label><label>确认后的候选人事实<textarea aria-label="确认后的候选人事实 JSON" disabled={readOnly} value={edit.facts} onChange={(event) => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, facts: event.target.value } }))} /></label>{draft.identityCandidateIds.length > 0 && <fieldset disabled={readOnly}><legend>身份候选：必须明确选择</legend><label><input checked={edit.mergeCandidateId === null} name={`identity-${draft.draftId}`} type="radio" value="new" onChange={() => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, mergeCandidateId: null } }))} />新建候选人</label>{draft.identityCandidateIds.map((candidateId) => <label key={candidateId}><input checked={edit.mergeCandidateId === candidateId} name={`identity-${draft.draftId}`} type="radio" value={candidateId} onChange={() => setEdits((items) => ({ ...items, [draft.draftId]: { ...edit, mergeCandidateId: candidateId } }))} />合并到 {candidateId}</label>)}</fieldset>}<button disabled={readOnly || !currentContextVersionId || !edit.stableName.trim() || edit.mergeCandidateId === undefined} type="submit">确认候选人</button></form>}</>}</article>; })}</section>
    <section aria-label="已确认候选人"><h3>已确认候选人</h3>{loadState === "ready" && relations.length === 0 && <p>暂无候选人</p>}{relations.map((item) => { const comparable = item.relation.contextVersionId === currentContextVersionId; return <article key={item.relation.positionCandidateId}><label><input disabled={readOnly || !comparable} name="candidate-comparison" type="checkbox" checked={comparisonIds.includes(item.relation.positionCandidateId)} onChange={() => setComparisonIds((ids) => ids.includes(item.relation.positionCandidateId) ? ids.filter((id) => id !== item.relation.positionCandidateId) : [...ids, item.relation.positionCandidateId])} />加入比较</label><button type="button" onClick={() => void openCandidate(item)}>查看{item.candidate.stableName}</button><small>岗位上下文 {item.relation.contextVersionId.slice(0, 8)}{!comparable && " · 上下文版本不同，需重算后比较"}</small></article>; })}<button disabled={readOnly || comparisonIds.length < 2 || !currentContextVersionId} type="button" onClick={() => void compare()}>比较已选候选人</button></section>
    {comparison && <section aria-label="候选人比较结果" className="hr-candidate-comparison"><h3>候选人比较结果</h3>{renderAnalysis(comparison)}</section>}
    {selected && <section aria-label="候选人详情" className="hr-candidate-detail"><header><div><span>CANDIDATE</span><h3>{selected.candidate.stableName}</h3></div><button type="button" onClick={() => setSelected(null)}>关闭详情</button></header><CandidateFacts facts={selected.candidate.facts} /><section aria-label="候选人材料"><h4>候选人材料（{selected.documents.length}）</h4>{[...selected.documents].sort((left, right) => right.versionNumber - left.versionNumber).map((document) => <article key={document.documentId}><strong>简历 v{document.versionNumber}</strong><small>{document.status === "active" ? "可预览和下载" : "已删除或保留期已结束"}</small>{document.status === "active" && <div><button disabled={readOnly} type="button" onClick={() => void openDocument(document, "preview")}>预览简历 v{document.versionNumber}</button><button disabled={readOnly} type="button" onClick={() => void openDocument(document, "download")}>下载简历 v{document.versionNumber}</button></div>}</article>)}</section><div className="hr-candidate-actions"><button disabled={readOnly || !currentContextVersionId || selected.relation.contextVersionId !== currentContextVersionId} type="button" onClick={() => void launch("candidate_match")}>生成匹配分析</button><button disabled={readOnly || !currentContextVersionId || selected.relation.contextVersionId !== currentContextVersionId} type="button" onClick={() => void launch("candidate_interview_plan")}>生成专属面试题</button><button type="button" onClick={() => void refreshAnalyses()}>刷新分析</button></div>{analysisTask && analysisTaskScope.current?.positionCandidateId === selected.relation.positionCandidateId && <p role="status">{TASK_LABEL[analysisTask.taskKind as keyof typeof TASK_LABEL]}：{taskStatus(analysisTask.status)}</p>}<section aria-label="分析历史"><h4>分析历史</h4>{orderedAnalyses.map(renderAnalysis)}</section>{newestAnalysis(orderedAnalyses) && <form onSubmit={(event) => { event.preventDefault(); void appendFeedback(); }}><label>人工纠正<textarea aria-label="人工纠正" disabled={readOnly} value={correction} onChange={(event) => setCorrection(event.target.value)} /></label><button disabled={readOnly || !correction.trim()} type="submit">记录人工纠正</button></form>}<section aria-label="人工反馈"><h4>人工反馈</h4>{selected.feedback.map((item) => <p key={item.feedbackId}>{item.correction ?? item.reason}</p>)}</section></section>}
    {!currentContextVersionId && <p role="status">确认岗位上下文后，才能确认候选人和生成岗位相对分析。</p>}
    {readOnly && <p role="status">当前为只读模式，不能上传、确认、分析或记录人工反馈。</p>}
    {notice && <p role={loadState === "error" ? "alert" : "status"}>{notice}{loadState === "error" && <>。<button type="button" onClick={() => setLoadAttempt((value) => value + 1)}>重试</button></>}</p>}
  </section>;
}
