/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AttachmentUploadClient } from "../../components/conversation/AttachmentUploader";
import { HrCandidateWorkspace } from "./HrCandidateWorkspace";

const positionId = "00000000-0000-4000-8000-000000000001";
const contextId = "00000000-0000-4000-8000-000000000002";
const draftIds = ["00000000-0000-4000-8000-000000000003", "00000000-0000-4000-8000-000000000004", "00000000-0000-4000-8000-000000000005"];
const attachmentIds = ["00000000-0000-4000-8000-000000000006", "00000000-0000-4000-8000-000000000007", "00000000-0000-4000-8000-000000000008"];
const candidateIds = ["00000000-0000-4000-8000-000000000009", "00000000-0000-4000-8000-00000000000a"];
const relationIds = ["00000000-0000-4000-8000-00000000000b", "00000000-0000-4000-8000-00000000000c"];
const analysisId = "00000000-0000-4000-8000-00000000000d";
const newestAnalysisId = "00000000-0000-4000-8000-00000000000e";
const otherContextId = "00000000-0000-4000-8000-00000000000f";
const artifactVersionId = "00000000-0000-4000-8000-000000000010";
const artifactAttachmentId = "00000000-0000-4000-8000-000000000011";
const conversationId = "00000000-0000-4000-8000-000000000012";
const now = "2026-09-04T00:00:00Z";
const draft = (index: number, state: "ready" | "failed" | "processing" = "ready") => ({ draftId: draftIds[index], positionId, attachmentId: attachmentIds[index], batchRequestId: contextId, state, extractedFacts: state === "ready" ? { stable_name: `候选人${index + 1}`, skills: ["Python"] } : {}, identityCandidateIds: [], errorCode: state === "failed" ? "parse_failed" : null, rowVersion: 2, createdAt: now, updatedAt: now });
const relation = (index: number) => ({ positionCandidateId: relationIds[index], positionId, candidateId: candidateIds[index], contextVersionId: contextId, sourceDraftId: draftIds[index], status: "active", rowVersion: 1, createdAt: now, updatedAt: now });
const candidate = (index: number) => ({ candidateId: candidateIds[index], stableName: `候选人${index + 1}`, facts: { skills: ["Python"] }, createdAt: now, updatedAt: now });
const candidateDocument = { documentId: attachmentIds[0], candidateId: candidateIds[0], attachmentId: attachmentIds[0], sourceDraftId: draftIds[0], documentKind: "resume", versionNumber: 1, contentSha256: "a".repeat(64), status: "active", createdAt: now };
const analysis = { analysisVersionId: analysisId, positionCandidateId: relationIds[0], positionId, candidateId: candidateIds[0], contextVersionId: contextId, versionNumber: 2, analysisKind: "match", documentIds: [candidateDocument.documentId], feedbackIds: [], result: { summary: "技能匹配", dimensions: { technical: "匹配" }, evidence: [{ resume_fact: "Python" }], gaps: [], risks: [], unknowns: ["量产规模"], verification_questions: ["请说明量产规模"] }, evidence: [{ resume_fact: "Python" }], unknowns: ["量产规模"], conflicts: [], verificationQuestions: ["请说明量产规模"], agentVersion: "hr-r12", modelVersion: "model", createdAt: now, sourceArtifactVersionId: null };
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });

function api() {
  return {
    candidateDrafts: vi.fn().mockResolvedValue([draft(0), draft(1), draft(2, "failed")]),
    retryDraft: vi.fn().mockResolvedValue({ ...draft(2), state: "processing" }),
    confirmDraft: vi.fn().mockResolvedValue({ candidate: candidate(0), document: candidateDocument, positionCandidate: relation(0) }),
    createCandidateDraftBatch: vi.fn().mockResolvedValue({ batchId: contextId, items: [draft(0)] }),
    positionCandidates: vi.fn().mockResolvedValue([relation(0), relation(1)]),
    candidate: vi.fn().mockImplementation((id: string) => Promise.resolve(candidate(candidateIds.indexOf(id)))),
    candidateDocuments: vi.fn().mockResolvedValue([candidateDocument]), candidateAnalyses: vi.fn().mockResolvedValue([analysis]),
    downloadCandidateDocument: vi.fn().mockResolvedValue({ contentPath: `/api/v1/attachments/content/${"a".repeat(32)}`, expiresAt: now }),
    resources: vi.fn().mockResolvedValue({ materials: [], artifacts: [] }),
    downloadResource: vi.fn().mockResolvedValue({ contentPath: `/api/v1/attachments/content/${"b".repeat(32)}`, expiresAt: now }),
    candidateFeedback: vi.fn().mockResolvedValue([]), appendCandidateFeedback: vi.fn().mockResolvedValue({ feedbackId: contextId, positionCandidateId: relationIds[0], analysisVersionId: analysisId, feedbackKind: "correction", conclusionKey: "overall", correction: "量产经验已电话核实", reason: "HR 人工核实", createdAt: now }),
    compareCandidates: vi.fn().mockResolvedValue({ ...analysis, analysisKind: "comparison", result: {
      candidates: [
        { position_candidate_id: relationIds[0], candidate_id: candidateIds[0], summary: "量产经验匹配", evidence_coverage: 2, unknown_count: 1 },
        { position_candidate_id: relationIds[1], candidate_id: candidateIds[1], summary: "交付经验匹配", evidence_coverage: 1, unknown_count: 2 },
      ], ranking: null, comparison_basis: "same_position_context",
    }, conflicts: ["项目规模口径不一致"] }), startTask: vi.fn().mockResolvedValue({ taskId: "task", status: "running", taskKind: "candidate_match", conversationId, turnId: draftIds[0], positionCandidateId: relationIds[0], candidateId: candidateIds[0] }), activeTasks: vi.fn().mockResolvedValue([]), taskStatus: vi.fn().mockResolvedValue({ taskId: "task", status: "completed", taskKind: "candidate_match", error: null, conversationId, turnId: draftIds[0], positionCandidateId: relationIds[0], candidateId: candidateIds[0] }),
  };
}

it("lists each resume version and safely preopens preview and retryable download tickets", async () => {
  const client = api();
  const navigations: string[] = [];
  const closed: boolean[] = [];
  vi.spyOn(window, "open").mockImplementation(() => ({
    opener: window,
    location: { replace: (path: string) => navigations.push(path) },
    close: () => closed.push(true),
  }) as unknown as Window);
  client.downloadCandidateDocument.mockImplementation(
    (_documentId: string, _requestId: string, purpose: "preview" | "download") => {
      if (purpose === "download" && client.downloadCandidateDocument.mock.calls.filter((call) => call[2] === "download").length === 1) {
        return Promise.reject(new Error("ticket temporarily unavailable"));
      }
      return Promise.resolve({ contentPath: `/api/v1/attachments/content/${purpose === "preview" ? "a" : "b"}`.padEnd(64, purpose === "preview" ? "a" : "b"), expiresAt: now });
    },
  );
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  expect(container.textContent).toContain("这份简历暂未解析完成，请重试。");
  expect(container.textContent).not.toContain("parse_failed");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());

  expect(container.textContent).toContain("简历 v1");
  const preview = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "预览简历 v1")!;
  const download = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载简历 v1")!;
  await act(async () => { preview.click(); await Promise.resolve(); });
  expect(client.downloadCandidateDocument).toHaveBeenLastCalledWith(
    candidateDocument.documentId, expect.any(String), "preview", expect.any(AbortSignal),
  );
  expect(navigations[navigations.length - 1]).toMatch(/^\/api\/v1\/attachments\/content\//);

  await act(async () => { download.click(); await Promise.resolve(); });
  const firstDownloadRequestId = client.downloadCandidateDocument.mock.calls[client.downloadCandidateDocument.mock.calls.length - 1]?.[1];
  expect(closed).toHaveLength(1);
  expect(container.textContent).toContain("简历下载未完成");
  await act(async () => { download.click(); await Promise.resolve(); });
  expect(client.downloadCandidateDocument.mock.calls[client.downloadCandidateDocument.mock.calls.length - 1]?.[1]).toBe(firstDownloadRequestId);
  expect(navigations[navigations.length - 1]).toMatch(/^\/api\/v1\/attachments\/content\//);
});

it("resolves each interview PDF download through a fresh Position resource and ticket request", async () => {
  const client = api();
  client.candidateAnalyses.mockResolvedValue([{ ...analysis,
    analysisKind: "candidate_interview_plan",
    result: { title: "专属面试题", questions: [{ verification_goal: "验证量产经验", candidate_reason: "简历提及量产", question: "请说明量产挑战。", follow_ups: [], strong_evidence: [], risk_signals: [] }] },
    evidence: [], unknowns: [], verificationQuestions: ["请说明量产挑战。"],
    sourceArtifactVersionId: artifactVersionId,
  }]);
  client.resources.mockResolvedValue({ materials: [], artifacts: [{
    artifactId: analysisId, artifactVersionId, attachmentId: artifactAttachmentId,
    artifactVersion: 1, filename: "面试题.pdf", mediaType: "application/pdf", state: "ready",
    sizeBytes: 1024, createdAt: now, sourceConversationId: null, sourceTurnId: null,
    previewAvailable: true, downloadAvailable: true,
  }] });
  const paths: string[] = [];
  vi.spyOn(window, "open").mockImplementation(() => ({ opener: window, location: { replace: (path: string) => paths.push(path) }, close: vi.fn() }) as unknown as Window);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  const download = container.querySelector<HTMLButtonElement>('[aria-label="下载面试题 PDF"]')!;
  await act(async () => { download.click(); await Promise.resolve(); });
  await act(async () => { download.click(); await Promise.resolve(); });

  expect(client.resources).toHaveBeenCalledTimes(2);
  expect(client.downloadResource).toHaveBeenCalledTimes(2);
  expect(client.downloadResource).toHaveBeenNthCalledWith(1, positionId, artifactAttachmentId, expect.any(String), "download", expect.any(AbortSignal));
  expect(client.downloadResource.mock.calls[0]?.[2]).not.toBe(client.downloadResource.mock.calls[1]?.[2]);
  expect(paths).toEqual([`/api/v1/attachments/content/${"b".repeat(32)}`, `/api/v1/attachments/content/${"b".repeat(32)}`]);
});

it("does not retain a failed interview PDF ticket request for the next retry", async () => {
  const client = api();
  client.candidateAnalyses.mockResolvedValue([{ ...analysis,
    analysisKind: "candidate_interview_plan",
    result: { title: "专属面试题", questions: [{ verification_goal: "验证量产经验", candidate_reason: "简历提及量产", question: "请说明量产挑战。", follow_ups: [], strong_evidence: [], risk_signals: [] }] },
    sourceArtifactVersionId: artifactVersionId,
  }]);
  client.resources.mockResolvedValue({ materials: [], artifacts: [{
    artifactId: analysisId, artifactVersionId, attachmentId: artifactAttachmentId,
    artifactVersion: 1, filename: "面试题.pdf", mediaType: "application/pdf", state: "ready",
    sizeBytes: 1024, createdAt: now, sourceConversationId: null, sourceTurnId: null,
    previewAvailable: true, downloadAvailable: true,
  }] });
  client.downloadResource.mockRejectedValueOnce(new Error("expired")).mockResolvedValueOnce({ contentPath: `/api/v1/attachments/content/${"b".repeat(32)}`, expiresAt: now });
  vi.spyOn(window, "open").mockImplementation(() => ({ opener: window, location: { replace: vi.fn() }, close: vi.fn() }) as unknown as Window);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  const download = container.querySelector<HTMLButtonElement>('[aria-label="下载面试题 PDF"]')!;

  await act(async () => download.click());
  await act(async () => download.click());

  expect(client.resources).toHaveBeenCalledTimes(2);
  expect(client.downloadResource.mock.calls[0]?.[2]).not.toBe(client.downloadResource.mock.calls[1]?.[2]);
});

it("keeps successful resume drafts when a sibling fails and retries only that item", async () => {
  const client = api();
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  expect(container.textContent?.match(/待确认/g)).toHaveLength(2);
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重试解析")?.click());
  expect(client.retryDraft).toHaveBeenCalledWith(draftIds[2], 2, expect.any(String), expect.any(AbortSignal));
  expect(container.textContent).toContain("正在解析");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "审阅候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "确认候选人")?.click());
  expect(client.confirmDraft).toHaveBeenCalledWith(draftIds[0], expect.objectContaining({ contextVersionId: contextId, stableName: "候选人1" }), expect.any(String), expect.any(AbortSignal));
});

it("uploads ready resume files and creates one recoverable batch", async () => {
  const client = api();
  const uploadClient: AttachmentUploadClient = {
    begin: vi.fn().mockResolvedValue({ uploadId: contextId, attachmentId: attachmentIds[0], conversationId: null, displayName: "resume.pdf", declaredMime: "application/pdf", declaredSize: 3, state: "uploading", uploadedBytes: 0, expiresAt: now }),
    upload: vi.fn().mockResolvedValue({}),
    complete: vi.fn().mockResolvedValue({ attachmentId: attachmentIds[0], conversationId: null, source: "user", displayName: "resume.pdf", detectedMime: "application/pdf", sizeBytes: 3, sha256: null, state: "ready", stateReason: null, createdAt: now, retainedUntil: now, preview: null, coverage: null }),
    cancel: vi.fn(),
  };
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} uploadClient={uploadClient} />));
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const file = new File(["pdf"], "resume.pdf", { type: "application/pdf" });
  await act(async () => { Object.defineProperty(input, "files", { configurable: true, value: [file] }); input.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => Promise.resolve());
  const parse = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent?.includes("开始解析"))!;
  await act(async () => parse.click());
  expect(client.createCandidateDraftBatch).toHaveBeenCalledWith(positionId, [attachmentIds[0]], expect.any(String), expect.any(AbortSignal));
});

it("loads candidate detail and versions, launches match/interview, records feedback, and compares through the frozen API", async () => {
  const client = api();
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} taskConversationId={conversationId} />));
  const first = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")!;
  await act(async () => first.click());
  expect(container.textContent).toContain("分析版本 v2");
  expect(container.textContent).toContain("待验证信息");
  expect(container.textContent).toContain("量产规模");
  expect(container.textContent).toContain("匹配证据");
  expect(container.textContent).toContain("简历事实：Python");
  expect(container.textContent).toContain("hr-r12 · model");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成专属面试题")?.click());
  expect(client.startTask).toHaveBeenCalledTimes(2);
  expect(client.startTask).toHaveBeenNthCalledWith(1, positionId, "candidate_match", expect.any(String), {
    contextVersionId: contextId,
    candidate: { candidateId: candidateIds[0], positionCandidateId: relationIds[0] },
    materialIds: [],
    conversationId,
  }, expect.any(AbortSignal));
  expect(client.startTask).toHaveBeenNthCalledWith(2, positionId, "candidate_interview_plan", expect.any(String), {
    contextVersionId: contextId,
    candidate: { candidateId: candidateIds[0], positionCandidateId: relationIds[0] },
    materialIds: [],
    conversationId,
  }, expect.any(AbortSignal));
  const textarea = container.querySelector<HTMLTextAreaElement>("textarea")!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "量产经验已电话核实"); textarea.dispatchEvent(new Event("input", { bubbles: true })); });
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "记录人工纠正")?.click());
  expect(client.appendCandidateFeedback).toHaveBeenCalledWith(relationIds[0], expect.objectContaining({ analysisVersionId: analysisId, correction: "量产经验已电话核实" }), expect.any(String), expect.any(AbortSignal));
  for (const checkbox of container.querySelectorAll<HTMLInputElement>('input[name="candidate-comparison"]')) await act(async () => checkbox.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "比较已选候选人")?.click());
  expect(client.compareCandidates).toHaveBeenCalledWith(positionId, relationIds, contextId, expect.any(String), expect.any(AbortSignal));
  expect(container.textContent).toContain("候选人比较结果");
  expect(container.textContent).toContain("项目规模口径不一致");
});

it("rejects a candidate task response bound to another conversation", async () => {
  const client = api();
  client.startTask.mockResolvedValue({
    taskId: "task", status: "completed", taskKind: "candidate_match", error: null,
    conversationId: otherContextId, turnId: draftIds[0],
    positionCandidateId: relationIds[0], candidateId: candidateIds[0],
  });
  await act(async () => root.render(<HrCandidateWorkspace
    api={client as never} csrfToken="csrf" currentContextVersionId={contextId}
    positionId={positionId} taskConversationId={conversationId}
  />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "查看候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "生成匹配分析")?.click());

  expect(client.candidateAnalyses).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("任务绑定异常");
});

it("starts a new task conversation when no preserved chat is supplied", async () => {
  const client = api();
  await act(async () => root.render(<HrCandidateWorkspace
    api={client as never} csrfToken="csrf" currentContextVersionId={contextId}
    positionId={positionId}
  />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "查看候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "生成匹配分析")?.click());

  expect(client.startTask).toHaveBeenCalledWith(
    positionId,
    "candidate_match",
    expect.any(String),
    {
      contextVersionId: contextId,
      candidate: {
        candidateId: candidateIds[0],
        positionCandidateId: relationIds[0],
      },
      materialIds: [],
    },
    expect.any(AbortSignal),
  );
});

it("renders the frozen comparison contract with candidate summaries and coverage instead of dropping nested data", async () => {
  const client = api();
  client.compareCandidates.mockResolvedValue({ ...analysis, analysisKind: "comparison", result: {
    candidates: [
      { position_candidate_id: relationIds[0], candidate_id: candidateIds[0], summary: "量产经验更完整", evidence_coverage: 3, unknown_count: 1 },
      { position_candidate_id: relationIds[1], candidate_id: candidateIds[1], summary: "海外交付更突出", evidence_coverage: 2, unknown_count: 2 },
    ],
    ranking: null,
    comparison_basis: "same_position_context",
  } });
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  for (const checkbox of container.querySelectorAll<HTMLInputElement>('input[name="candidate-comparison"]')) await act(async () => checkbox.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "比较已选候选人")?.click());

  for (const text of ["同一岗位上下文", "候选人1", "量产经验更完整", "证据覆盖", "3 条", "待验证项", "1 项", "候选人2", "海外交付更突出", "未提供单一排序"]) {
    expect(container.textContent).toContain(text);
  }
  expect(container.querySelector(".hr-candidate-comparison pre")).toBeNull();
  expect(container.textContent).not.toContain('"candidates":');
});

it("renders the frozen resume-extract envelope and bounds unknown nested fallback content", async () => {
  const client = api();
  client.candidateAnalyses.mockResolvedValue([{ ...analysis, analysisKind: "resume_extract", result: {
    extracted_facts: {
      stable_name: "候选人1", summary: "结构与量产复合背景", skills: ["Python", "机械设计"],
      experiences: [{ company: "星海制造", role: "结构工程师", achievements: ["良率提升至 98%"] }],
      projects: [{ name: "喷嘴量产", details: { responsibility: "主导验证" } }],
      unknowns: Array.from({ length: 60 }, (_, index) => `补充事实 ${index + 1}`),
      sources: [{ document_id: candidateDocument.documentId, custom_signal: ["可迁移的量产方法"] }],
    },
    identity_candidate_ids: [],
  } }]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());

  for (const text of ["简历提取", "候选人身份", "候选人1", "结构与量产复合背景", "技能", "Python", "经历", "星海制造", "良率提升至 98%", "喷嘴量产", "主导验证", "可迁移的量产方法", "其余 10 项未展开"]) {
    expect(container.textContent).toContain(text);
  }
  expect(container.textContent).not.toContain("补充事实 60");
  expect(container.querySelector(".hr-candidate-detail pre")).toBeNull();
  expect(container.textContent).not.toContain('"experiences":');
});

it("normalizes and filters protected legacy keys while bounding and escaping safe fallback text", async () => {
  const client = api();
  const longKey = `${"😀".repeat(550)}尾部键名`;
  const longValue = `${"🧪".repeat(550)}尾部内容`;
  const htmlText = '<script>alert("legacy")</script><img src=x onerror=alert(1)>';
  const protectedKeys = [
    "age", "birthdate", "dateofbirth", "disability", "ethnicity", "gender", "health",
    "maritalstatus", "nationality", "onboarding", "offerstatus", "pipelinestage",
    "politicalaffiliation", "pregnancy", "race", "religion", "sexualorientation",
    "storagekey", "storagepath", "objectkey", "objectref", "objectrefciphertext",
    "immutablelocator", "ats", "atsid", "interviewschedule", "automaticrejection",
    "beisen", "bosszhipin", "liepin", "年龄", "出生日期", "生日", "残疾", "残障",
    "民族", "性别", "健康", "健康状况", "婚姻", "婚姻状况", "婚育", "国籍", "入职",
    "录用状态", "流程阶段", "政治面貌", "怀孕", "孕期", "种族", "宗教", "性取向",
    "存储键", "存储路径", "对象键", "对象引用", "不可变定位符", "面试安排", "自动淘汰",
    "object_ref", "storage-path", "immutable-locator", "Object_Ref/Ciphertext",
    "OBJECT.REF", "ＳＴＯＲＡＧＥ＿ＰＡＴＨ", "Immutable/Locator", "A_T:S", "PIPELINE-STAGE",
    "Boß-ZhiPin", `storage\u0085path`,
  ];
  const protectedPayload = Object.fromEntries(protectedKeys.map((key, index) => [key, `SECRET_PROTECTED_${index}`]));
  const nestedProtectedValues = ["SECRET_NESTED_REF", "SECRET_NESTED_GENDER"];
  client.candidateAnalyses.mockResolvedValue([{ ...analysis, analysisKind: "resume_extract", result: {
    extracted_facts: {
      stable_name: "候选人1", summary: "正常摘要",
      ["constructor"]: "正常构造字段", ["__proto__"]: "正常原型字段",
      ["object\uFEFFref"]: "正常 FEFF 字段",
      safe_profile: { ordinary_field: "正常嵌套字段", html_note: htmlText, nested: {
        object_ref: nestedProtectedValues[0], deeper: { "ＧＥＮＤＥＲ": nestedProtectedValues[1] },
      } },
      [longKey]: longValue,
      deep: { level_1: { level_2: { level_3: { level_4: { level_5: "深层秘密" } } } } },
      ...protectedPayload,
    },
    identity_candidate_ids: [],
  } }]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());

  for (const value of [...Object.values(protectedPayload), ...nestedProtectedValues]) expect(container.textContent).not.toContain(value);
  expect(container.textContent).toContain("正常嵌套字段");
  expect(container.textContent).toContain("正常构造字段");
  expect(container.textContent).toContain("正常原型字段");
  expect(container.textContent).toContain("正常 FEFF 字段");
  expect(container.textContent).toContain(htmlText);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  const renderedLabels = [...container.querySelectorAll(".hr-candidate-legacy-fields dt")].map((item) => item.textContent);
  for (const protectedLabel of ["object ref", "storage-path", "immutable-locator", "Object Ref/Ciphertext"]) {
    expect(renderedLabels).not.toContain(protectedLabel);
  }
  expect(container.textContent).not.toContain("尾部键名");
  expect(container.textContent).not.toContain("尾部内容");
  expect(container.textContent).not.toContain("深层秘密");
  expect(container.textContent).toContain("内容层级过深，未展开");
  const truncatedKey = [...container.querySelectorAll(".hr-candidate-legacy-fields dt")].find((item) => item.textContent?.startsWith("😀😀😀"));
  expect(Array.from(truncatedKey?.textContent ?? "")).toHaveLength(500);
  expect(truncatedKey?.textContent?.endsWith("…")).toBe(true);
  const truncatedValue = truncatedKey?.parentElement?.querySelector("dd span")?.textContent ?? "";
  expect(Array.from(truncatedValue)).toHaveLength(500);
  expect(truncatedValue.endsWith("…")).toBe(true);
});

it("polls processing drafts with bounded delay and stops at a terminal state", async () => {
  vi.useFakeTimers(); const client = api();
  client.candidateDrafts.mockReset().mockResolvedValueOnce([draft(0, "processing")]).mockResolvedValueOnce([draft(0)]);
  client.positionCandidates.mockResolvedValue([]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  expect(container.textContent).toContain("正在解析");
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(container.textContent).toContain("待确认");
  const calls = client.candidateDrafts.mock.calls.length;
  await act(async () => vi.advanceTimersByTimeAsync(20_000));
  expect(client.candidateDrafts).toHaveBeenCalledTimes(calls);
});

it("stops automatic draft polling after the bounded retry budget and leaves manual refresh available", async () => {
  vi.useFakeTimers(); const client = api();
  client.candidateDrafts.mockReset().mockImplementation(() => Promise.resolve([draft(0, "processing")]));
  client.positionCandidates.mockResolvedValue([]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  for (const delay of [1_000, 2_000, 4_000, 8_000, 8_000, 8_000]) {
    await act(async () => vi.advanceTimersByTimeAsync(delay));
  }
  expect(client.candidateDrafts).toHaveBeenCalledTimes(7);
  expect(container.textContent).toContain("自动刷新已暂停");
  expect([...container.querySelectorAll<HTMLButtonElement>("button")].some((button) => button.textContent === "刷新候选人状态")).toBe(true);
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(client.candidateDrafts).toHaveBeenCalledTimes(7);
});

it("starts a fresh polling budget after retrying a failed draft", async () => {
  vi.useFakeTimers(); const client = api();
  client.candidateDrafts.mockReset().mockImplementation(() => Promise.resolve([draft(0, "processing"), draft(2, "failed")]));
  client.positionCandidates.mockResolvedValue([]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  for (const delay of [1_000, 2_000, 4_000, 8_000, 8_000, 8_000]) await act(async () => vi.advanceTimersByTimeAsync(delay));
  const exhaustedCalls = client.candidateDrafts.mock.calls.length;
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重试解析")?.click());
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(client.candidateDrafts.mock.calls.length).toBeGreaterThan(exhaustedCalls);
});

it("reuses the candidate task idempotency key after an uncertain start failure", async () => {
  const client = api();
  client.startTask.mockRejectedValueOnce({ status: 503 }).mockResolvedValueOnce({ taskId: "task", status: "running", taskKind: "candidate_match", error: null });
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  const launch = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")!;
  await act(async () => launch.click());
  await act(async () => launch.click());
  expect(client.startTask.mock.calls[0]?.[2]).toBe(client.startTask.mock.calls[1]?.[2]);
});

it("retains separate idempotency keys for interleaved uncertain task starts", async () => {
  const client = api();
  client.startTask.mockRejectedValueOnce({ status: 503 }).mockRejectedValueOnce({ status: 503 }).mockResolvedValueOnce({ taskId: "task", status: "running", taskKind: "candidate_match", error: null });
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  const match = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")!;
  const interview = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成专属面试题")!;
  await act(async () => match.click()); await act(async () => interview.click()); await act(async () => match.click());
  expect(client.startTask.mock.calls[0]?.[2]).toBe(client.startTask.mock.calls[2]?.[2]);
  expect(client.startTask.mock.calls[0]?.[2]).not.toBe(client.startTask.mock.calls[1]?.[2]);
});

it("previews and edits extracted facts and requires an explicit identity decision", async () => {
  const client = api();
  client.candidateDrafts.mockResolvedValue([{ ...draft(0), extractedFacts: { stable_name: "候选人1", skills: ["Python"], unknowns: ["量产规模"] }, identityCandidateIds: [candidateIds[1]] }]);
  client.positionCandidates.mockResolvedValue([]);
  client.confirmDraft.mockRejectedValueOnce({ status: 409 }).mockResolvedValueOnce({ candidate: candidate(0), document: candidateDocument, positionCandidate: relation(0) });
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "审阅候选人1")?.click());
  expect(container.textContent).toContain(`来源附件 ${attachmentIds[0]}`);
  expect(container.textContent).toContain("待人工核实：量产规模");
  const confirm = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "确认候选人")!;
  expect(confirm.disabled).toBe(true);
  const name = container.querySelector<HTMLInputElement>('[aria-label="候选人称谓"]')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(name, "候选人甲"); name.dispatchEvent(new Event("input", { bubbles: true })); });
  const facts = container.querySelector<HTMLTextAreaElement>('[aria-label="确认后的候选人事实 JSON"]')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(facts, '{"skills":["Python","C++"],"unknowns":[]}'); facts.dispatchEvent(new Event("input", { bubbles: true })); });
  await act(async () => container.querySelector<HTMLInputElement>(`input[value="${candidateIds[1]}"]`)?.click());
  await act(async () => confirm.click());
  expect(container.textContent).toContain("身份或版本已变化");
  expect(name.value).toBe("候选人甲");
  await act(async () => confirm.click());
  expect(client.confirmDraft).toHaveBeenLastCalledWith(draftIds[0], expect.objectContaining({ stableName: "候选人甲", confirmedFacts: { skills: ["Python", "C++"], unknowns: [] }, mergeCandidateId: candidateIds[1] }), expect.any(String), expect.any(AbortSignal));
});

it("binds feedback to the maximum analysis version and excludes another context from comparison", async () => {
  const client = api();
  const newest = { ...analysis, analysisVersionId: newestAnalysisId, versionNumber: 5, createdAt: "2026-09-04T02:00:00Z" };
  client.candidateAnalyses.mockResolvedValue([analysis, newest]);
  client.positionCandidates.mockResolvedValue([relation(0), { ...relation(1), contextVersionId: otherContextId }]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  const textarea = container.querySelector<HTMLTextAreaElement>('[aria-label="人工纠正"]')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "以最新分析为准"); textarea.dispatchEvent(new Event("input", { bubbles: true })); });
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "记录人工纠正")?.click());
  expect(client.appendCandidateFeedback).toHaveBeenCalledWith(relationIds[0], expect.objectContaining({ analysisVersionId: newestAnalysisId }), expect.any(String), expect.any(AbortSignal));
  const comparisons = container.querySelectorAll<HTMLInputElement>('input[name="candidate-comparison"]');
  expect(comparisons[0]?.disabled).toBe(false);
  expect(comparisons[1]?.disabled).toBe(true);
  expect(container.textContent).toContain("上下文版本不同，需重算后比较");
});

it("refreshes analysis after a durable candidate task reaches completion", async () => {
  vi.useFakeTimers(); const client = api();
  const refreshed = { ...analysis, analysisVersionId: newestAnalysisId, versionNumber: 3 };
  client.candidateAnalyses.mockResolvedValueOnce([analysis]).mockResolvedValueOnce([analysis, refreshed]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")?.click());
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(container.textContent).toContain("分析版本 v3");
});

it("refreshes analysis when an idempotent candidate task replay is already completed", async () => {
  const client = api();
  const refreshed = { ...analysis, analysisVersionId: newestAnalysisId, versionNumber: 3 };
  client.startTask.mockResolvedValue({ taskId: "task", status: "completed", taskKind: "candidate_match", error: null });
  client.candidateAnalyses.mockResolvedValueOnce([analysis]).mockResolvedValueOnce([analysis, refreshed]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")?.click());
  expect(container.textContent).toContain("分析版本 v3");
  expect(container.textContent).toContain("已完成，分析版本已刷新");
});

it("does not claim a completed candidate task failed to start when analysis refresh is unavailable", async () => {
  const client = api();
  client.startTask.mockResolvedValue({ taskId: "task", status: "completed", taskKind: "candidate_match", error: null });
  client.candidateAnalyses.mockResolvedValueOnce([analysis]).mockRejectedValueOnce(new Error("temporarily unavailable"));
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")?.click());
  expect(container.textContent).toContain("任务已完成，分析暂时无法刷新");
  expect(container.textContent).not.toContain("任务未启动");
});

it("fails closed when terminal task kind does not match the launched candidate task", async () => {
  vi.useFakeTimers(); const client = api();
  client.taskStatus.mockResolvedValue({ taskId: "task", status: "completed", taskKind: "candidate_interview_plan", error: null, positionCandidateId: relationIds[0], candidateId: candidateIds[0] });
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")?.click());
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(client.candidateAnalyses).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("任务绑定异常");
});

it("uses authoritative terminal failure and keeps it isolated from another selected candidate", async () => {
  vi.useFakeTimers(); const client = api();
  client.taskStatus.mockResolvedValue({ taskId: "task", status: "failed", taskKind: "candidate_match", error: "模型失败", positionCandidateId: relationIds[0], candidateId: candidateIds[0] });
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人2")?.click());
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(client.taskStatus).toHaveBeenCalledWith(positionId, "task", expect.any(AbortSignal));
  expect(client.candidateAnalyses).toHaveBeenCalledTimes(2);
  expect(container.textContent).not.toContain("模型失败");
  expect(container.textContent).not.toContain("已完成，分析版本已刷新");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  expect(container.textContent).toContain("模型失败");
});

it("shows complete structured result, evidence and exact provenance identifiers", async () => {
  const client = api();
  client.candidateAnalyses.mockResolvedValue([{ ...analysis,
    result: { summary: "技能匹配", dimensions: { architecture: "strong" }, evidence: [{ claim: "Python", source: { document_id: candidateDocument.documentId, locator: "page:2" } }], gaps: [], risks: [], unknowns: [], verification_questions: [] },
    evidence: [{ claim: "Python", source: { document_id: candidateDocument.documentId, locator: "page:2" } }], sourceArtifactVersionId: null,
    feedbackIds: [newestAnalysisId],
  }]);
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  expect(container.textContent).toContain("architecture");
  expect(container.textContent).toContain("strong");
  expect(container.textContent).toContain("page:2");
  expect(container.textContent).toContain(candidateDocument.documentId);
  expect(container.textContent).toContain(contextId);
  expect(container.textContent).toContain(newestAnalysisId);
});

it("renders candidate facts as labeled text instead of a raw JSON dump", async () => {
  const client = api();
  client.candidate.mockResolvedValue({ ...candidate(0), facts: { skills: ["Python"], experience: { years: 5 } } });
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());

  expect(container.querySelector(".hr-candidate-detail > pre")).toBeNull();
  expect(container.textContent).toContain("技能");
  expect(container.textContent).toContain("Python");
  expect(container.textContent).toContain("经历");
  expect(container.textContent).not.toContain('"skills":');
});

it("explains why analysis retry is unavailable instead of exposing a silent no-op", async () => {
  const client = api();
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={null} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重新生成")).toBeUndefined();
  expect(container.textContent).toContain("确认岗位上下文后才能重新生成此分析");

  await act(async () => root.unmount());
  root = createRoot(container);
  const mismatched = api();
  await act(async () => root.render(<HrCandidateWorkspace api={mismatched as never} csrfToken="csrf" currentContextVersionId={otherContextId} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  expect(container.textContent).toContain("候选人的岗位上下文已变化，刷新后再重新生成");
  expect(mismatched.startTask).not.toHaveBeenCalled();
});

it("disables upload and all candidate mutations in hard-stale read-only mode", async () => {
  const client = api();
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} readOnly />));
  expect(container.querySelector<HTMLInputElement>('input[type="file"]')?.disabled).toBe(true);
  const mutationButtons = [...container.querySelectorAll<HTMLButtonElement>("button")].filter((button) => ["重试解析", "比较已选候选人"].includes(button.textContent ?? ""));
  expect(mutationButtons.every((button) => button.disabled)).toBe(true);
  await act(async () => mutationButtons[0]?.click());
  expect(client.retryDraft).not.toHaveBeenCalled();
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")?.click());
  const documentButtons = [...container.querySelectorAll<HTMLButtonElement>("button")].filter((button) => /^(预览|下载)简历 v1$/.test(button.textContent ?? ""));
  expect(documentButtons).toHaveLength(2);
  expect(documentButtons.every((button) => button.disabled)).toBe(true);
  await act(async () => documentButtons[0]?.click());
  expect(client.downloadCandidateDocument).not.toHaveBeenCalled();
});

it("distinguishes an empty candidate set from a local read failure", async () => {
  const client = api();
  client.candidateDrafts.mockResolvedValue([]);
  client.positionCandidates.mockResolvedValue([]);
  await act(async () => root.render(<HrCandidateWorkspace
    api={client as never} csrfToken="csrf" currentContextVersionId={null} positionId={positionId}
  />));
  expect(container.textContent).toContain("暂无候选人");
  expect(container.textContent).not.toContain("候选人数据暂时不可用");

  await act(async () => root.unmount());
  root = createRoot(container);
  const failed = api(); failed.candidateDrafts.mockRejectedValue(new Error("offline"));
  await act(async () => root.render(<HrCandidateWorkspace
    api={failed as never} csrfToken="csrf" currentContextVersionId={null} positionId={positionId}
  />));
  expect(container.textContent).toContain("候选人数据暂时无法读取");
  expect([...container.querySelectorAll<HTMLButtonElement>("button")].some((button) => button.textContent === "重试")).toBe(true);
});
