import { useState } from "react";
import { Download } from "lucide-react";

import type { ConversationFeedbackRating, ConversationFeedbackReason } from "../../conversationTypes";
import { MessageActions } from "../../components/conversation/MessageActions";
import type {
  HrCandidateAnalysisVersion, HrCandidateInterviewPlanResult, HrCandidateMatchResult,
} from "../../hrR12Types";

const FIELD_LABELS: Record<string, string> = {
  claim: "结论", resume_fact: "简历事实", source: "来源", document_id: "简历版本",
  page: "页码", technical: "技术能力", experience: "经历", delivery: "交付能力",
};

function fieldLabel(value: string): string {
  return FIELD_LABELS[value] ?? value.replace(/_/g, " ");
}

function valueText(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(valueText).filter(Boolean).join("；");
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${fieldLabel(key)}：${valueText(item)}`).join("；");
  return "未提供";
}

function Evidence({ items }: { items: Record<string, unknown>[] }) {
  return <ul>{items.length === 0 ? <li>暂未提供明确证据</li> : items.map((item, index) => <li key={`${index}:${valueText(item)}`}>{valueText(item)}</li>)}</ul>;
}

function TextList({ empty, items }: { empty: string; items: string[] }) {
  return <ul>{items.length === 0 ? <li>{empty}</li> : items.map((item) => <li key={item}>{item}</li>)}</ul>;
}

function matchCopy(result: HrCandidateMatchResult): string {
  return [
    `匹配结论\n${result.summary}`,
    `匹配维度\n${Object.entries(result.dimensions).map(([key, value]) => `${fieldLabel(key)}：${valueText(value)}`).join("\n") || "暂未提供"}`,
    `匹配证据\n${result.evidence.map(valueText).join("\n") || "暂未提供明确证据"}`,
    `能力差距\n${result.gaps.join("\n") || "未发现明确差距"}`,
    `风险提示\n${result.risks.join("\n") || "未发现明确风险"}`,
    `待验证信息\n${result.unknowns.join("\n") || "暂无待验证信息"}`,
    `核实问题\n${result.verification_questions.join("\n") || "暂无核实问题"}`,
  ].join("\n\n");
}

function interviewCopy(result: HrCandidateInterviewPlanResult): string {
  return [result.title, ...result.questions.flatMap((item, index) => [
    `${index + 1}. ${item.question}`,
    `验证目标：${item.verification_goal}`,
    `出题原因：${item.candidate_reason}`,
    `递进追问：${item.follow_ups.join("；") || "无"}`,
    `有效证据：${item.strong_evidence.join("；") || "无"}`,
    `风险信号：${item.risk_signals.join("；") || "无"}`,
  ])].join("\n");
}

export function HrCandidateAnalysisCard({
  analysis, feedbackState, onCopy, onDownload, onFeedback, onRetry, retryUnavailableReason, readOnly = false,
}: {
  analysis: HrCandidateAnalysisVersion;
  feedbackState?: ConversationFeedbackRating | "pending" | "error";
  onCopy?: (text: string) => Promise<boolean>;
  onDownload?: () => Promise<void> | void;
  onFeedback?: (rating: ConversationFeedbackRating, reason: ConversationFeedbackReason | null, comment: string | null) => void;
  onRetry?: () => void;
  retryUnavailableReason?: string;
  readOnly?: boolean;
}) {
  const [downloadState, setDownloadState] = useState<"idle" | "pending" | "error">("idle");
  const match = analysis.analysisKind === "match";
  const copyText = () => match
    ? matchCopy(analysis.result as HrCandidateMatchResult)
    : interviewCopy(analysis.result as HrCandidateInterviewPlanResult);
  const download = async () => {
    if (!onDownload || downloadState === "pending") return;
    setDownloadState("pending");
    try { await onDownload(); setDownloadState("idle"); }
    catch { setDownloadState("error"); }
  };

  return <article aria-label={`候选人${match ? "匹配分析" : "专属面试题"} v${analysis.versionNumber}`} className="hr-candidate-analysis-card">
    <header><div><span>分析版本 v{analysis.versionNumber}</span><h5>{match ? "岗位匹配分析" : (analysis.result as HrCandidateInterviewPlanResult).title}</h5></div><time dateTime={analysis.createdAt}>{new Date(analysis.createdAt).toLocaleString("zh-CN")}</time></header>
    {match ? (() => {
      const result = analysis.result as HrCandidateMatchResult;
      return <div className="hr-candidate-analysis-body">
        <section className="is-summary"><h6>匹配结论</h6><p>{result.summary}</p></section>
        <section><h6>匹配维度</h6><dl>{Object.entries(result.dimensions).map(([key, value]) => <div key={key}><dt>{fieldLabel(key)}</dt><dd>{valueText(value)}</dd></div>)}</dl>{Object.keys(result.dimensions).length === 0 && <p>暂未提供</p>}</section>
        <section><h6>匹配证据</h6><Evidence items={result.evidence} /></section>
        <section><h6>能力差距</h6><TextList empty="未发现明确差距" items={result.gaps} /></section>
        <section><h6>风险提示</h6><TextList empty="未发现明确风险" items={result.risks} /></section>
        <section><h6>待验证信息</h6><TextList empty="暂无待验证信息" items={result.unknowns} /></section>
        <section><h6>核实问题</h6><TextList empty="暂无核实问题" items={result.verification_questions} /></section>
      </div>;
    })() : <div className="hr-candidate-analysis-body hr-interview-plan">
      {(analysis.result as HrCandidateInterviewPlanResult).questions.map((item, index) => <section key={`${index}:${item.question}`}>
        <span>问题 {index + 1}</span><h6>{item.question}</h6>
        <dl><div><dt>验证目标</dt><dd>{item.verification_goal}</dd></div><div><dt>针对该候选人的原因</dt><dd>{item.candidate_reason}</dd></div></dl>
        <div className="hr-interview-question-lists"><section><h6>递进追问</h6><TextList empty="无" items={item.follow_ups} /></section><section><h6>有效证据</h6><TextList empty="无" items={item.strong_evidence} /></section><section><h6>风险信号</h6><TextList empty="无" items={item.risk_signals} /></section></div>
      </section>)}
    </div>}
    <div className="hr-candidate-analysis-footer">
      <dl className="hr-candidate-analysis-provenance"><div><dt>来源岗位版本</dt><dd>{analysis.contextVersionId}</dd></div><div><dt>来源简历版本</dt><dd>{analysis.documentIds.join("、")}</dd></div>{analysis.feedbackIds.length > 0 && <div><dt>参考反馈版本</dt><dd>{analysis.feedbackIds.join("、")}</dd></div>}<div><dt>生成方</dt><dd>{analysis.agentVersion} · {analysis.modelVersion}</dd></div></dl>
      {!match && (analysis.sourceArtifactVersionId === null
        ? <p className="hr-candidate-pdf-missing" role="status">PDF 尚未生成，重试本任务</p>
        : <button aria-label="下载面试题 PDF" className="hr-candidate-pdf-download" disabled={readOnly || downloadState === "pending" || !onDownload} onClick={() => void download()} type="button"><Download size={16} />{downloadState === "pending" ? "正在准备 PDF…" : "下载面试题 PDF"}</button>)}
      {downloadState === "error" && <p role="alert">PDF 下载未完成，请重试本任务</p>}
      {retryUnavailableReason && <p className="hr-candidate-retry-unavailable" role="status">{retryUnavailableReason}</p>}
      <MessageActions copyText={copyText} feedbackState={feedbackState} onCopy={onCopy} onFeedback={readOnly ? undefined : onFeedback} onRetry={readOnly ? undefined : onRetry} presentation="icon" />
    </div>
  </article>;
}
