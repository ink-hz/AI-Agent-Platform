/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import type { AgentCapabilityCard } from "../../brainTypes";
import type { AttachmentUploadClient } from "../../components/conversation/AttachmentUploader";
import type { ConversationAttachment } from "../../conversationTypes";
import { createHrR12Api, type HrR12Api } from "../../hrR12Api";
import type { HrCandidateAnalysisVersion, HrCandidateDraft, HrPositionArtifactItem, HrPositionCandidate } from "../../hrR12Types";
import { HrCandidateWorkspace } from "./HrCandidateWorkspace";
import { HrPositionWorkspace } from "./HrPositionWorkspace";

const positionId = "00000000-0000-4000-8000-000000000001";
const materialId = "00000000-0000-4000-8000-000000000002";
const contextId = "00000000-0000-4000-8000-000000000003";
const artifactId = "00000000-0000-4000-8000-000000000004";
const artifactAttachmentId = "00000000-0000-4000-8000-000000000005";
const candidateArtifactVersionId = "00000000-0000-4000-8000-000000000006";
const artifactVersionId = "00000000-0000-4000-8000-000000000007";
const now = "2026-09-04T00:00:00Z";
const account: Account = {
  internal_user_id: "member", display_name: "HR", role: "member", departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};
const card: AgentCapabilityCard = {
  agent_id: "hr-bot", display_name: "HR Agent", domain_group: "HR", persona_subtitle: "招聘协作",
  mission: "招聘智能协作", capabilities: [], exclusions: [], example_tasks: [], required_inputs: [],
  accepted_input_types: ["text"], output_types: ["text"], supports_attachments_in: false,
  supports_attachments_out: false, attachment_limits: null, supports_evidence: true,
  supports_streaming: true, supports_cancellation: true, supports_idempotency: true,
  max_duration_seconds: 300, data_classification: "internal", adapter_id: "metabot-core-chat",
  capability_version: 1, adapter_kind: "metabot_local", adapter_config_version: 1,
  output_contract: "normalized_task_result_v1", interaction_modes: ["direct_chat"], workspace_url: null,
};
const confirmedContext = {
  contextVersionId: contextId, positionId, displayVersion: 1, status: "confirmed" as const,
  summary: "已确认高级结构工程师画像", modules: { talent_profile: { summary: "喷嘴和挤出工艺" } },
  officialVersionId: null, baseContextVersionId: null, sourceConversationId: null, sourceTurnId: null,
  sourceArtifactVersionId: null, sourceMaterialAttachmentIds: [materialId], agentId: "hr-bot",
  modelVersion: "model", rowVersion: 1, createdAt: now, confirmedAt: now,
};
const basePosition = {
  positionId, sourceKind: "manual" as const, officialJobId: null, title: "3D打印机高级结构工程师",
  department: "研发", locations: ["深圳"], officialStatus: null, internalStatus: "active" as const,
  sourceVersion: null, rowVersion: 1, createdAt: now, updatedAt: now, conversationCount: 0,
  materialCount: 1, artifactCount: 0, conversationIds: [], materialAttachmentIds: [materialId],
  artifactIds: [], artifactAttachmentIds: [],
};
const material = {
  attachmentId: materialId, filename: "岗位说明.pdf", mediaType: "application/pdf", state: "ready",
  sizeBytes: 2, createdAt: now, sourceConversationId: null, sourceTurnId: null,
  previewAvailable: true, downloadAvailable: true,
};
const artifact = {
  ...material, artifactId, artifactVersionId, attachmentId: artifactAttachmentId, filename: "人才画像.docx",
  mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  artifactVersion: 1, previewAvailable: false,
} satisfies HrPositionArtifactItem;

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  sessionStorage.clear();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  sessionStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((item) => item.textContent === label || item.textContent?.startsWith(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found;
}

async function click(label: string) {
  await act(async () => button(label).click());
}

async function flushMicrotasks(rounds = 8) {
  await act(async () => {
    for (let index = 0; index < rounds; index += 1) await Promise.resolve();
  });
}

function workspaceHarness() {
  const state = { taskStarted: false, activeReads: 0, completed: false };
  const position = vi.fn().mockImplementation(() => Promise.resolve(state.completed ? {
    ...basePosition, artifactCount: 1, artifactIds: [artifactId], artifactAttachmentIds: [artifactAttachmentId],
  } : basePosition));
  const resources = vi.fn().mockImplementation(() => Promise.resolve({
    materials: [material], artifacts: state.completed ? [artifact] : [],
  }));
  const startTask = vi.fn().mockImplementation(async () => {
    state.taskStarted = true;
    return { taskId: "position-task", status: "running", taskKind: "talent_profile", error: null };
  });
  const activeTasks = vi.fn().mockImplementation(async () => {
    if (!state.taskStarted) return [];
    state.activeReads += 1;
    if (state.activeReads === 1) return [{ taskId: "position-task", status: "running", taskKind: "talent_profile", error: null }];
    state.completed = true;
    return [];
  });
  const r12Api: HrR12Api = {
    ...createHrR12Api(account.csrf_token),
    officialVersions: vi.fn().mockResolvedValue([]), officialVersion: vi.fn(), downloadOfficialVersion: vi.fn(),
    startTask, activeTasks, taskStatus: vi.fn(), resources,
    downloadResource: vi.fn().mockResolvedValue({ contentPath: `/api/attachments/content/ticket-${artifactAttachmentId}`, expiresAt: now }),
    context: vi.fn().mockResolvedValue({ current: confirmedContext, drafts: [], history: [confirmedContext] }),
    compareContext: vi.fn(), confirmContext: vi.fn(), candidateDrafts: vi.fn().mockResolvedValue([]),
    positionCandidates: vi.fn().mockResolvedValue([]), candidate: vi.fn(), candidateDocuments: vi.fn(),
    candidateAnalyses: vi.fn(), candidateFeedback: vi.fn(), retryDraft: vi.fn(), confirmDraft: vi.fn(),
    createCandidateDraftBatch: vi.fn(), appendCandidateFeedback: vi.fn(), compareCandidates: vi.fn(),
  };
  const onOpenConversation = vi.fn();
  const props = {
    account, positionId, section: "chat" as const,
    api: { position, promoteMaterial: vi.fn(), removeMaterial: vi.fn() },
    r12Api, loadPositionConversations: vi.fn().mockResolvedValue([]),
    loadCatalog: vi.fn().mockResolvedValue([card]), onOpenConversation,
  };
  return { onOpenConversation, position, props, r12Api, resources, startTask };
}

describe("HR R1.2 position journey", () => {
  it("keeps chat as the main canvas while details and position tasks stay on demand", async () => {
    vi.useFakeTimers();
    const journey = workspaceHarness();
    await act(async () => root.render(<HrPositionWorkspace {...journey.props} />));
    expect(container.querySelector(".hr-position-bar")).not.toBeNull();
    expect(container.querySelector(".agent-use-workspace.is-focused")).not.toBeNull();
    expect(container.querySelector(".hr-position-context-metrics")).toBeNull();
    expect(container.querySelector(".hr-position-sections")).toBeNull();
    expect(container.querySelector(".hr-position-taskbar")).toBeNull();
    expect(container.textContent).not.toContain("当前没有执行中任务");
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "保留岗位会话草稿");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await click("岗位资料");
    expect(container.querySelector('section[aria-label="内部岗位理解"]')).not.toBeNull();
    await click("候选人");
    expect(container.querySelector('section[aria-label="批量简历导入"] input[type="file"]')).not.toBeNull();
    await click("材料与成果");
    expect(container.querySelector('section[aria-label="岗位材料与成果"]')).not.toBeNull();
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(textarea.value).toBe("保留岗位会话草稿");
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="关闭岗位资料"]')?.click());
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(textarea.value).toBe("保留岗位会话草稿");
    expect(journey.onOpenConversation).not.toHaveBeenCalled();

    await click("岗位任务");
    const selectedMaterial = container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')!;
    expect(selectedMaterial.checked).toBe(false);
    await act(async () => selectedMaterial.click());
    await click("生成人才画像");
    expect(journey.startTask).toHaveBeenCalledWith(positionId, "talent_profile", expect.any(String), {
      contextVersionId: contextId, materialIds: [materialId], conversationId: undefined,
    }, expect.any(AbortSignal));

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<HrPositionWorkspace {...journey.props} section="artifacts" />));
    expect(container.textContent).toContain("人才画像：执行中");
    const resourcesBeforeCompletion = journey.resources.mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(container.textContent).not.toContain("当前没有执行中任务");
    expect(container.querySelector('[aria-label="岗位任务状态"]')).toBeNull();
    expect(journey.position).toHaveBeenCalledTimes(3);
    await click("岗位资料");
    await click("材料与成果");
    expect(journey.resources.mock.calls.length).toBeGreaterThan(resourcesBeforeCompletion);
    expect(container.textContent).toContain("人才画像.docx");
    expect(container.textContent).toContain("成果（1）");

    const replace = vi.fn();
    vi.spyOn(window, "open").mockReturnValue({ opener: null, close: vi.fn(), location: { replace } } as never);
    await click("下载人才画像.docx");
    expect(journey.r12Api.downloadResource).toHaveBeenCalledWith(
      positionId, artifactAttachmentId, expect.any(String), "download", expect.any(AbortSignal),
    );
    expect(replace).toHaveBeenCalledWith(`/api/attachments/content/ticket-${artifactAttachmentId}`);
  });
});

const resumeAttachmentIds = [
  "10000000-0000-4000-8000-000000000001", "10000000-0000-4000-8000-000000000002",
  "10000000-0000-4000-8000-000000000003",
];
const draftIds = [
  "20000000-0000-4000-8000-000000000001", "20000000-0000-4000-8000-000000000002",
  "20000000-0000-4000-8000-000000000003",
];
const candidateIds = ["30000000-0000-4000-8000-000000000001", "30000000-0000-4000-8000-000000000002"];
const relationIds = ["40000000-0000-4000-8000-000000000001", "40000000-0000-4000-8000-000000000002"];
const documentIds = ["50000000-0000-4000-8000-000000000001", "50000000-0000-4000-8000-000000000002"];
const analysisIds = ["60000000-0000-4000-8000-000000000001", "60000000-0000-4000-8000-000000000002"];

function draft(index: number, state: HrCandidateDraft["state"] = "ready"): HrCandidateDraft {
  return {
    draftId: draftIds[index], positionId, attachmentId: resumeAttachmentIds[index], batchRequestId: contextId,
    state, extractedFacts: state === "ready" ? { stable_name: `候选人${index + 1}`, skills: ["结构设计"] } : {},
    identityCandidateIds: [], errorCode: state === "failed" ? "parse_failed" : null,
    rowVersion: 1, createdAt: now, updatedAt: now,
  };
}

function candidate(index: number) {
  return { candidateId: candidateIds[index], stableName: `候选人${index + 1}`, facts: { skills: ["结构设计"] }, createdAt: now, updatedAt: now };
}

function relation(index: number): HrPositionCandidate {
  return {
    positionCandidateId: relationIds[index], positionId, candidateId: candidateIds[index], contextVersionId: contextId,
    sourceDraftId: draftIds[index], status: "active", rowVersion: 1, createdAt: now, updatedAt: now,
  };
}

function candidateDocument(index: number) {
  return {
    documentId: documentIds[index], candidateId: candidateIds[index], attachmentId: resumeAttachmentIds[index],
    sourceDraftId: draftIds[index], documentKind: "resume", versionNumber: 1,
    contentSha256: String(index + 1).repeat(64), status: "active" as const, createdAt: now,
  };
}

function analysis(index: number, kind: "match" | "candidate_interview_plan", versionNumber: number): HrCandidateAnalysisVersion {
  const common = {
    analysisVersionId: analysisIds[versionNumber - 1], positionCandidateId: relationIds[index], positionId,
    candidateId: candidateIds[index], contextVersionId: contextId, versionNumber,
    documentIds: [documentIds[index]], feedbackIds: [],
    evidence: [{ document_id: documentIds[index], locator: "page:2" }], unknowns: [], conflicts: [],
    verificationQuestions: [], agentVersion: "hr-r12", modelVersion: "model", createdAt: now,
  };
  if (kind === "match") return {
    ...common, analysisKind: kind,
    result: {
      summary: "喷嘴结构经验匹配", dimensions: { technical: "匹配" },
      evidence: [{ resume_fact: "有喷嘴结构经验" }], gaps: [], risks: [], unknowns: [],
      verification_questions: [],
    }, sourceArtifactVersionId: null,
  };
  return {
    ...common, analysisKind: kind,
    result: { title: "候选人专属面试题", questions: [{
      verification_goal: "验证挤出工艺能力", candidate_reason: "简历提及喷嘴结构经验",
      question: "如何控制挤出背压？", follow_ups: ["如何验证稳定性？"],
      strong_evidence: ["说明可量化指标"], risk_signals: ["无法区分本人贡献"],
    }] },
    sourceArtifactVersionId: candidateArtifactVersionId,
  };
}

function readyAttachment(index: number): ConversationAttachment {
  return {
    attachmentId: resumeAttachmentIds[index], conversationId: null, source: "user",
    displayName: `候选人${index + 1}.pdf`, detectedMime: "application/pdf", sizeBytes: 3,
    sha256: null, state: "ready", stateReason: null, createdAt: now, retainedUntil: now,
    preview: null, coverage: null,
  };
}

function uploadClient(): AttachmentUploadClient {
  return {
    begin: vi.fn().mockImplementation(async (_conversationId, file: File) => {
      const index = Number(file.name.match(/(\d+)/)?.[1] ?? "1") - 1;
      return { uploadId: `upload-${index}`, attachmentId: resumeAttachmentIds[index], conversationId: null,
        displayName: file.name, declaredMime: file.type, declaredSize: file.size, state: "uploading",
        uploadedBytes: 0, expiresAt: now };
    }),
    upload: vi.fn().mockResolvedValue({}),
    complete: vi.fn().mockImplementation(async (uploadId: string) => readyAttachment(Number(uploadId.split("-")[1]))),
    cancel: vi.fn(),
  };
}

function candidateHarness(preconfirmed = false) {
  const analyses = new Map<string, HrCandidateAnalysisVersion[]>(relationIds.map((id) => [id, []]));
  const taskKinds = new Map<string, "candidate_match" | "candidate_interview_plan">();
  let delayedAnalysisRefresh = false;
  const api = {
    candidateDrafts: vi.fn().mockResolvedValue([]),
    createCandidateDraftBatch: vi.fn().mockResolvedValue({ batchId: contextId, items: [draft(0), draft(1), draft(2, "failed")] }),
    retryDraft: vi.fn().mockResolvedValue(draft(2)),
    confirmDraft: vi.fn().mockImplementation(async (draftId: string) => {
      const index = draftIds.indexOf(draftId);
      return { candidate: candidate(index), document: candidateDocument(index), positionCandidate: relation(index) };
    }),
    positionCandidates: vi.fn().mockResolvedValue(preconfirmed ? [relation(0), relation(1)] : []),
    candidate: vi.fn().mockImplementation(async (id: string) => candidate(candidateIds.indexOf(id))),
    candidateDocuments: vi.fn().mockImplementation(async (id: string) => [candidateDocument(candidateIds.indexOf(id))]),
    candidateAnalyses: vi.fn().mockImplementation((positionCandidateId: string, signal?: AbortSignal) => {
      const result = [...(analyses.get(positionCandidateId) ?? [])];
      if (!delayedAnalysisRefresh) return Promise.resolve(result);
      delayedAnalysisRefresh = false;
      return new Promise<HrCandidateAnalysisVersion[]>((resolve, reject) => {
        const timer = window.setTimeout(() => resolve(result), 10);
        signal?.addEventListener("abort", () => { window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
      });
    }),
    candidateFeedback: vi.fn().mockResolvedValue([]),
    appendCandidateFeedback: vi.fn().mockImplementation(async (positionCandidateId: string, input: { analysisVersionId: string; correction: string }) => ({
      feedbackId: "70000000-0000-4000-8000-000000000001", positionCandidateId,
      analysisVersionId: input.analysisVersionId, feedbackKind: "correction", conclusionKey: "overall",
      correction: input.correction, reason: "HR 人工核实", createdAt: now,
    })),
    startTask: vi.fn().mockImplementation(async (_positionId: string, kind: "candidate_match" | "candidate_interview_plan") => {
      const taskId = `task-${kind}`;
      taskKinds.set(taskId, kind);
      return { taskId, status: "running", taskKind: kind, error: null };
    }),
    taskStatus: vi.fn().mockImplementation(async (_positionId: string, taskId: string) => {
      const kind = taskKinds.get(taskId)!;
      const next = analysis(0, kind === "candidate_match" ? "match" : "candidate_interview_plan", kind === "candidate_match" ? 1 : 2);
      analyses.set(relationIds[0], [...(analyses.get(relationIds[0]) ?? []).filter((item) => item.analysisVersionId !== next.analysisVersionId), next]);
      delayedAnalysisRefresh = true;
      return { taskId, status: "completed", taskKind: kind, error: null,
        positionCandidateId: relationIds[0], candidateId: candidateIds[0] };
    }),
    compareCandidates: vi.fn().mockResolvedValue({
      ...analysis(0, "match", 1), analysisVersionId: "60000000-0000-4000-8000-000000000003",
      analysisKind: "comparison", versionNumber: 3, result: {
        candidates: [
          { position_candidate_id: relationIds[0], candidate_id: candidateIds[0], summary: "候选人1更匹配", evidence_coverage: 2, unknown_count: 0 },
          { position_candidate_id: relationIds[1], candidate_id: candidateIds[1], summary: "结构经验需补充", evidence_coverage: 1, unknown_count: 1 },
        ],
        ranking: null, comparison_basis: "same_position_context",
      },
      conflicts: ["量产规模口径待统一"],
    }),
  };
  return { api };
}

describe("HR R1.2 candidate journey", () => {
  it("submits three resumes as one batch, retries only the failed item, and confirms two candidates", async () => {
    const journey = candidateHarness();
    await act(async () => root.render(<HrCandidateWorkspace api={journey.api as never} csrfToken="csrf"
      currentContextVersionId={contextId} positionId={positionId} uploadClient={uploadClient()} />));
    const fileInput = container.querySelector<HTMLInputElement>('section[aria-label="批量简历导入"] input[type="file"]')!;
    const files = [1, 2, 3].map((number) => new File(["pdf"], `候选人${number}.pdf`, { type: "application/pdf" }));
    await act(async () => {
      Object.defineProperty(fileInput, "files", { configurable: true, value: files });
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flushMicrotasks();
    expect(button("开始解析 3 份简历").disabled).toBe(false);
    await click("开始解析 3 份简历");
    expect(journey.api.createCandidateDraftBatch).toHaveBeenCalledWith(
      positionId, resumeAttachmentIds, expect.any(String), expect.any(AbortSignal),
    );
    expect(container.textContent?.match(/待确认/g)).toHaveLength(2);
    expect(container.textContent).toContain("解析失败");
    await click("重试解析");
    expect(journey.api.retryDraft).toHaveBeenCalledWith(draftIds[2], 1, expect.any(String), expect.any(AbortSignal));
    expect(container.textContent?.match(/待确认/g)).toHaveLength(3);

    await click("审阅候选人1");
    await click("确认候选人");
    await click("审阅候选人2");
    await click("确认候选人");
    expect(journey.api.confirmDraft).toHaveBeenCalledTimes(2);
    expect(journey.api.confirmDraft).toHaveBeenNthCalledWith(1, draftIds[0],
      expect.objectContaining({ contextVersionId: contextId, stableName: "候选人1" }), expect.any(String), expect.any(AbortSignal));
    expect(journey.api.confirmDraft).toHaveBeenNthCalledWith(2, draftIds[1],
      expect.objectContaining({ contextVersionId: contextId, stableName: "候选人2" }), expect.any(String), expect.any(AbortSignal));
    expect(container.textContent).toContain("2 位已确认");
  });

  it("refreshes match and interview results at terminal state, records correction, and compares two candidates in one context", async () => {
    const journey = candidateHarness(true);
    await act(async () => root.render(<HrCandidateWorkspace api={journey.api as never} csrfToken="csrf"
      currentContextVersionId={contextId} positionId={positionId} />));
    await click("查看候选人1");
    vi.useFakeTimers();
    await click("生成匹配分析");
    await act(async () => vi.advanceTimersByTimeAsync(1_010));
    expect(container.textContent).toContain("喷嘴结构经验匹配");
    expect(container.textContent).toContain("匹配分析已完成，分析版本已刷新");
    await click("生成专属面试题");
    await act(async () => vi.advanceTimersByTimeAsync(1_010));
    expect(container.textContent).toContain("如何控制挤出背压？");
    expect(container.textContent).toContain("候选人专属面试题已完成，分析版本已刷新");

    const correction = container.querySelector<HTMLTextAreaElement>('[aria-label="人工纠正"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(correction, "量产规模已由面试官核实");
      correction.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("记录人工纠正");
    expect(journey.api.appendCandidateFeedback).toHaveBeenCalledWith(relationIds[0],
      expect.objectContaining({ analysisVersionId: analysisIds[1], correction: "量产规模已由面试官核实" }),
      expect.any(String), expect.any(AbortSignal));
    expect(container.textContent).toContain("量产规模已由面试官核实");

    for (const checkbox of container.querySelectorAll<HTMLInputElement>('input[name="candidate-comparison"]')) {
      await act(async () => checkbox.click());
    }
    await click("比较已选候选人");
    expect(journey.api.compareCandidates).toHaveBeenCalledWith(positionId, relationIds, contextId,
      expect.any(String), expect.any(AbortSignal));
    expect(container.textContent).toContain("候选人1更匹配");
    expect(container.textContent).toContain("证据覆盖");
    expect(container.textContent).toContain("冲突：量产规模口径待统一");
  });

  it("keeps hard-stale writes disabled and fails closed on a cross-candidate terminal binding", async () => {
    const staleJourney = candidateHarness(true);
    staleJourney.api.candidateDrafts.mockResolvedValue([draft(2, "failed")]);
    await act(async () => root.render(<HrCandidateWorkspace api={staleJourney.api as never} csrfToken="csrf"
      currentContextVersionId={contextId} positionId={positionId} readOnly />));
    expect(container.querySelector<HTMLInputElement>('input[type="file"]')?.disabled).toBe(true);
    expect(button("重试解析").disabled).toBe(true);
    expect([...container.querySelectorAll<HTMLInputElement>('input[name="candidate-comparison"]')]
      .every((input) => input.disabled)).toBe(true);

    await act(async () => root.unmount());
    root = createRoot(container);
    const crossed = candidateHarness(true);
    crossed.api.taskStatus.mockResolvedValue({ taskId: "task-candidate_match", status: "completed",
      taskKind: "candidate_match", error: null, positionCandidateId: relationIds[1], candidateId: candidateIds[1] });
    await act(async () => root.render(<HrCandidateWorkspace api={crossed.api as never} csrfToken="csrf"
      currentContextVersionId={contextId} positionId={positionId} />));
    await click("查看候选人1");
    const initialAnalysisReads = crossed.api.candidateAnalyses.mock.calls.length;
    vi.useFakeTimers();
    await click("生成匹配分析");
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(container.textContent).toContain("候选人任务绑定异常");
    expect(crossed.api.candidateAnalyses).toHaveBeenCalledTimes(initialAnalysisReads);
  });
});
