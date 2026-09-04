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
const now = "2026-09-04T00:00:00Z";
const draft = (index: number, state: "ready" | "failed" | "processing" = "ready") => ({ draftId: draftIds[index], positionId, attachmentId: attachmentIds[index], batchRequestId: contextId, state, extractedFacts: state === "ready" ? { stable_name: `候选人${index + 1}`, skills: ["Python"] } : {}, identityCandidateIds: [], errorCode: state === "failed" ? "parse_failed" : null, rowVersion: 2, createdAt: now, updatedAt: now });
const relation = (index: number) => ({ positionCandidateId: relationIds[index], positionId, candidateId: candidateIds[index], contextVersionId: contextId, sourceDraftId: draftIds[index], status: "active", rowVersion: 1, createdAt: now, updatedAt: now });
const candidate = (index: number) => ({ candidateId: candidateIds[index], stableName: `候选人${index + 1}`, facts: { skills: ["Python"] }, createdAt: now, updatedAt: now });
const candidateDocument = { documentId: attachmentIds[0], candidateId: candidateIds[0], attachmentId: attachmentIds[0], sourceDraftId: draftIds[0], documentKind: "resume", versionNumber: 1, contentSha256: "a".repeat(64), status: "active", createdAt: now };
const analysis = { analysisVersionId: analysisId, positionCandidateId: relationIds[0], positionId, candidateId: candidateIds[0], contextVersionId: contextId, versionNumber: 2, analysisKind: "match", documentIds: [candidateDocument.documentId], feedbackIds: [], result: { summary: "技能匹配" }, evidence: [{ claim: "Python" }], unknowns: ["量产规模"], conflicts: [], verificationQuestions: ["请说明量产规模"], agentVersion: "hr-r12", modelVersion: "model", createdAt: now };
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

function api() {
  return {
    candidateDrafts: vi.fn().mockResolvedValue([draft(0), draft(1), draft(2, "failed")]),
    retryDraft: vi.fn().mockResolvedValue({ ...draft(2), state: "processing" }),
    confirmDraft: vi.fn().mockResolvedValue({ candidate: candidate(0), document: candidateDocument, positionCandidate: relation(0) }),
    createCandidateDraftBatch: vi.fn().mockResolvedValue({ batchId: contextId, items: [draft(0)] }),
    positionCandidates: vi.fn().mockResolvedValue([relation(0), relation(1)]),
    candidate: vi.fn().mockImplementation((id: string) => Promise.resolve(candidate(candidateIds.indexOf(id)))),
    candidateDocuments: vi.fn().mockResolvedValue([candidateDocument]), candidateAnalyses: vi.fn().mockResolvedValue([analysis]),
    candidateFeedback: vi.fn().mockResolvedValue([]), appendCandidateFeedback: vi.fn().mockResolvedValue({ feedbackId: contextId, positionCandidateId: relationIds[0], analysisVersionId: analysisId, feedbackKind: "correction", conclusionKey: "overall", correction: "量产经验已电话核实", reason: "HR 人工核实", createdAt: now }),
    compareCandidates: vi.fn().mockResolvedValue({ ...analysis, analysisKind: "comparison" }), startTask: vi.fn().mockResolvedValue({ taskId: "task", status: "running", taskKind: "candidate_match" }),
  };
}

it("keeps successful resume drafts when a sibling fails and retries only that item", async () => {
  const client = api();
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  expect(container.textContent?.match(/待确认/g)).toHaveLength(2);
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重试解析")?.click());
  expect(client.retryDraft).toHaveBeenCalledWith(draftIds[2], 2, expect.any(String), expect.any(AbortSignal));
  expect(container.textContent).toContain("正在解析");
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
  await act(async () => root.render(<HrCandidateWorkspace api={client as never} csrfToken="csrf" currentContextVersionId={contextId} positionId={positionId} />));
  const first = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "查看候选人1")!;
  await act(async () => first.click());
  expect(container.textContent).toContain("分析版本 v2");
  expect(container.textContent).toContain("未验证：量产规模");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成匹配分析")?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成专属面试题")?.click());
  expect(client.startTask).toHaveBeenCalledTimes(2);
  expect(client.startTask).toHaveBeenNthCalledWith(1, positionId, "candidate_match", expect.any(String), {
    contextVersionId: contextId,
    candidate: { candidateId: candidateIds[0], positionCandidateId: relationIds[0] },
    materialIds: [],
  }, expect.any(AbortSignal));
  expect(client.startTask).toHaveBeenNthCalledWith(2, positionId, "candidate_interview_plan", expect.any(String), {
    contextVersionId: contextId,
    candidate: { candidateId: candidateIds[0], positionCandidateId: relationIds[0] },
    materialIds: [],
  }, expect.any(AbortSignal));
  const textarea = container.querySelector<HTMLTextAreaElement>("textarea")!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "量产经验已电话核实"); textarea.dispatchEvent(new Event("input", { bubbles: true })); });
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "记录人工纠正")?.click());
  expect(client.appendCandidateFeedback).toHaveBeenCalledWith(relationIds[0], expect.objectContaining({ analysisVersionId: analysisId, correction: "量产经验已电话核实" }), expect.any(String), expect.any(AbortSignal));
  for (const checkbox of container.querySelectorAll<HTMLInputElement>('input[name="candidate-comparison"]')) await act(async () => checkbox.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "比较已选候选人")?.click());
  expect(client.compareCandidates).toHaveBeenCalledWith(positionId, relationIds, contextId, expect.any(String), expect.any(AbortSignal));
});
