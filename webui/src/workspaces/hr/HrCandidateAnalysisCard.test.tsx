/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrCandidateAnalysisVersion } from "../../hrR12Types";
import { HrCandidateAnalysisCard } from "./HrCandidateAnalysisCard";

const analysis = {
  analysisVersionId: "00000000-0000-4000-8000-000000000001",
  positionCandidateId: "00000000-0000-4000-8000-000000000002",
  positionId: "00000000-0000-4000-8000-000000000003",
  candidateId: "00000000-0000-4000-8000-000000000004",
  contextVersionId: "00000000-0000-4000-8000-000000000005",
  versionNumber: 2,
  analysisKind: "match",
  documentIds: ["00000000-0000-4000-8000-000000000006"],
  feedbackIds: [],
  result: {
    summary: "总体匹配",
    dimensions: { technical: "技术能力匹配" },
    evidence: [{ resume_fact: "负责挤出系统" }],
    gaps: ["海外交付经历不足"],
    risks: ["团队规模不明确"],
    unknowns: ["量产良率经验待验证"],
    verification_questions: ["请说明量产良率。"],
  },
  evidence: [{ resume_fact: "负责挤出系统" }],
  unknowns: ["量产良率经验待验证"],
  conflicts: [], verificationQuestions: ["请说明量产良率。"],
  agentVersion: "hr-bot", modelVersion: "model-v1",
  createdAt: "2026-09-04T00:00:00Z", sourceArtifactVersionId: null,
} satisfies HrCandidateAnalysisVersion;

describe("HrCandidateAnalysisCard", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("renders match evidence, gaps, risks, unknowns and provenance as business text", async () => {
    const onCopy = vi.fn().mockResolvedValue(true);
    const onFeedback = vi.fn(); const onRetry = vi.fn();
    await act(async () => root.render(<HrCandidateAnalysisCard
      analysis={analysis} onCopy={onCopy} onFeedback={onFeedback} onRetry={onRetry}
    />));

    for (const text of ["匹配证据", "负责挤出系统", "能力差距", "海外交付经历不足", "风险提示", "团队规模不明确", "待验证信息", "量产良率经验待验证", "核实问题", "请说明量产良率。", "来源岗位版本", "来源简历版本"]) {
      expect(container.textContent).toContain(text);
    }
    expect(container.querySelector("pre")).toBeNull();
    expect(container.textContent).not.toContain('{"');
    expect(container.querySelector("footer footer")).toBeNull();
    expect(container.querySelector(".hr-candidate-analysis-footer")?.tagName).toBe("DIV");

    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="复制回答"]')?.click());
    expect(onCopy).toHaveBeenCalledWith(expect.stringContaining("匹配证据"));
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="不达标"]')?.click());
    const comment = container.querySelector<HTMLTextAreaElement>('[aria-label="补充改进建议"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(comment, "量产结论缺少证据");
      comment.dispatchEvent(new Event("input", { bubbles: true }));
      [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "信息不完整")?.click();
    });
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "提交反馈")?.click());
    expect(onFeedback).toHaveBeenCalledWith("unhelpful", "incomplete", "量产结论缺少证据");
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重新生成")?.click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows interview questions and makes the PDF the only download action", async () => {
    const plan: HrCandidateAnalysisVersion = {
      ...analysis, analysisKind: "candidate_interview_plan",
      result: { title: "结构工程师专属面试题", questions: [{
        verification_goal: "验证量产经验", candidate_reason: "简历提及量产",
        question: "请说明量产挑战。", follow_ups: ["良率如何？"],
        strong_evidence: ["给出量化指标"], risk_signals: ["无法说明本人贡献"],
      }] },
      evidence: [], unknowns: [], verificationQuestions: ["请说明量产挑战。"],
      sourceArtifactVersionId: null,
    };
    await act(async () => root.render(<HrCandidateAnalysisCard analysis={plan} />));
    expect(container.textContent).toContain("PDF 尚未生成，重试本任务");
    expect(container.querySelectorAll('button[aria-label*="下载"]')).toHaveLength(0);
    expect(container.textContent).toContain("验证量产经验");
    expect(container.textContent).toContain("给出量化指标");

    const onDownload = vi.fn().mockResolvedValue(undefined);
    await act(async () => root.render(<HrCandidateAnalysisCard analysis={{
      ...plan, sourceArtifactVersionId: "00000000-0000-4000-8000-000000000007",
    }} onDownload={onDownload} />));
    const downloads = container.querySelectorAll<HTMLButtonElement>('button[aria-label="下载面试题 PDF"]');
    expect(downloads).toHaveLength(1);
    await act(async () => { downloads[0].click(); await Promise.resolve(); });
    expect(onDownload).toHaveBeenCalledTimes(1);
  });
});
