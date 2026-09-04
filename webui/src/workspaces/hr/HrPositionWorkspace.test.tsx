/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import type { AgentCapabilityCard } from "../../brainTypes";
import type { Conversation, ConversationSubmissionResult } from "../../conversationTypes";
import type { HrPositionDetail } from "../../hrTypes";
import { HrPositionWorkspace } from "./HrPositionWorkspace";

vi.mock("../../attachmentApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../attachmentApi")>();
  const upload = {
    uploadId: "upload-position-draft", attachmentId: "attachment-position-draft", conversationId: null,
    displayName: "待发送JD.pdf", declaredMime: "application/pdf", declaredSize: 2,
    state: "uploading" as const, uploadedBytes: 2, expiresAt: "2026-09-04T12:00:00Z",
  };
  return {
    ...actual,
    beginAttachmentUpload: vi.fn().mockResolvedValue(upload),
    uploadAttachmentContent: vi.fn().mockResolvedValue(upload),
    completeAttachmentUpload: vi.fn().mockResolvedValue({
      attachmentId: upload.attachmentId, conversationId: null, source: "user", displayName: upload.displayName,
      detectedMime: upload.declaredMime, sizeBytes: upload.declaredSize, sha256: null, state: "ready",
      stateReason: null, createdAt: "2026-09-04T10:00:00Z", retainedUntil: "2027-09-04T10:00:00Z",
      preview: { attachmentId: upload.attachmentId, detectedMime: upload.declaredMime }, coverage: null,
    }),
    fetchConversationAttachment: vi.fn(),
    cancelAttachmentUpload: vi.fn().mockResolvedValue(undefined),
  };
});


const POSITION_ID = "11111111-1111-4111-8111-111111111111";
const ACTIVE_ID = "22222222-2222-4222-8222-222222222222";
const ARCHIVED_ID = "33333333-3333-4333-8333-333333333333";
const OUTSIDER_ID = "44444444-4444-4444-8444-444444444444";
const account: Account = {
  internal_user_id: "member", display_name: "HR", role: "member", departments: [],
  gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};
const detail: HrPositionDetail = {
  positionId: POSITION_ID, sourceKind: "official_site", officialJobId: "J11014",
  title: "3D 打印高级结构工程师", department: "研发", locations: ["深圳", "中山"],
  officialStatus: "active", internalStatus: "active", sourceVersion: "sync-v2", rowVersion: 2,
  createdAt: "2026-09-01T00:00:00Z", updatedAt: "2026-09-04T00:00:00Z",
  conversationCount: 2, materialCount: 1, artifactCount: 3,
  conversationIds: [ACTIVE_ID, ARCHIVED_ID], materialAttachmentIds: [], artifactIds: [],
  artifactAttachmentIds: [],
};
const card: AgentCapabilityCard = {
  agent_id: "hr-bot", display_name: "HR Agent", domain_group: "HR",
  persona_subtitle: "Hannah · 技术人才搜寻与招聘协作", mission: "招聘智能协作",
  capabilities: ["岗位分析"], exclusions: ["不代替录用决策"], example_tasks: ["梳理岗位画像"],
  required_inputs: ["岗位目标"], accepted_input_types: ["text"], output_types: ["text"],
  supports_attachments_in: false, supports_attachments_out: false, attachment_limits: null,
  supports_evidence: true, supports_streaming: true, supports_cancellation: true,
  supports_idempotency: true, max_duration_seconds: 300, data_classification: "internal",
  adapter_id: "metabot-core-chat", capability_version: 1, adapter_kind: "metabot_local",
  adapter_config_version: 1, output_contract: "normalized_task_result_v1",
  interaction_modes: ["direct_chat"], workspace_url: null,
};

function conversation(conversationId: string, title: string, status: "active" | "archived"): Conversation {
  return {
    conversation_id: conversationId, mode: "direct_agent", direct_agent_id: "hr-bot", title, status,
    summary_through_seq: 0, created_at: "2026-09-03T10:00:00Z", updated_at: "2026-09-04T10:00:00Z",
    archived_at: status === "archived" ? "2026-09-04T11:00:00Z" : null,
  };
}

const active = conversation(ACTIVE_ID, "喷嘴与挤出工艺面试", "active");
const archived = conversation(ARCHIVED_ID, "历史岗位画像", "archived");
const outsider = conversation(OUTSIDER_ID, "其他岗位候选人", "active");

function submissionResult(): ConversationSubmissionResult {
  return {
    conversation: conversation(ACTIVE_ID, "新岗位对话", "active"),
    message: {
      message_id: "message", conversation_id: ACTIVE_ID, seq: 1, role: "user", content: "梳理面试方案",
      turn_id: "turn", delivery_status: "accepted", created_at: "2026-09-04T10:00:00Z",
      completed_at: null, input_attachments: [], output_attachments: [], active_attachment_ids: [],
    },
    turn: {
      turn_id: "turn", conversation_id: ACTIVE_ID, user_message_id: "message", assistant_message_id: null,
      retry_of_turn_id: null, status: "accepted", created_at: "2026-09-04T10:00:00Z",
      updated_at: "2026-09-04T10:00:00Z",
    },
  };
}

async function clickButton(container: HTMLElement, label: string) {
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === label || button.textContent?.startsWith(label))?.click());
}

async function openTaskMenu(container: HTMLElement) {
  await clickButton(container, "岗位任务");
}

function dependencies() {
  const r12Api = {
    resources: vi.fn().mockResolvedValue({ materials: [{ attachmentId: "55555555-5555-4555-8555-555555555555", filename: "岗位说明.pdf", mediaType: "application/pdf", state: "ready", sizeBytes: 10, createdAt: "2026-09-04T00:00:00Z", sourceConversationId: null, sourceTurnId: null, previewAvailable: true, downloadAvailable: true }], artifacts: [] }),
    context: vi.fn().mockResolvedValue({ current: null, drafts: [], history: [] }), activeTasks: vi.fn().mockResolvedValue([]), taskStatus: vi.fn(), startTask: vi.fn().mockResolvedValue({ taskId: "task", status: "running", taskKind: "jd", error: null }),
    confirmContext: vi.fn(), compareContext: vi.fn(), candidateDrafts: vi.fn().mockResolvedValue([]), positionCandidates: vi.fn().mockResolvedValue([]), candidate: vi.fn(), candidateDocuments: vi.fn(), candidateAnalyses: vi.fn(), candidateFeedback: vi.fn(), retryDraft: vi.fn(), confirmDraft: vi.fn(), createCandidateDraftBatch: vi.fn(), createCandidateAnalysis: vi.fn(), appendCandidateFeedback: vi.fn(), compareCandidates: vi.fn(), downloadResource: vi.fn(), downloadCandidateDocument: vi.fn(),
  };
  return {
    api: { position: vi.fn().mockResolvedValue(detail), promoteMaterial: vi.fn(), removeMaterial: vi.fn() },
    conversationClient: {
      fetchConversation: vi.fn().mockImplementation((conversationId: string) => Promise.resolve({
        conversation: conversationId === ARCHIVED_ID ? archived : active,
        current_turn: null,
      })),
      fetchMessages: vi.fn().mockResolvedValue([]),
      createMessageSubmission: vi.fn(), fetchTaskDetail: vi.fn(), streamEvents: vi.fn().mockResolvedValue(undefined),
      cancelCurrentTurn: vi.fn(), confirmAction: vi.fn(), rejectAction: vi.fn(), submitFeedback: vi.fn(),
      retryTurn: vi.fn(), reconnectDelay: vi.fn().mockResolvedValue(undefined),
    } as never,
    loadPositionConversations: vi.fn().mockResolvedValue([active, archived, outsider]),
    loadCatalog: vi.fn().mockResolvedValue([card]),
    createSubmission: vi.fn().mockReturnValue({ idempotencyKey: "same", send: vi.fn().mockResolvedValue(submissionResult()) }),
    onOpenConversation: vi.fn(),
    r12Api,
  };
}

describe("HrPositionWorkspace", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it("renders one chat-first position workspace and keeps its composer mounted through the details drawer", async () => {
    const deps = dependencies();
    deps.loadCatalog.mockResolvedValue([{ ...card,
      accepted_input_types: ["text", "image", "pdf", "office"], supports_attachments_in: true,
      attachment_limits: {
        max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 5,
        max_bytes_per_message: 50 * 1024 * 1024, max_files_per_conversation: 50,
        max_bytes_per_conversation: 500 * 1024 * 1024,
      },
    }]);
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));

    expect(container.querySelector(".hr-position-workspace")?.getAttribute("data-position-id")).toBe(POSITION_ID);
    expect(container.querySelector(".hr-position-bar")).not.toBeNull();
    expect(container.querySelectorAll(".agent-use-workspace")).toHaveLength(1);
    expect(container.querySelector(".agent-use-workspace.is-focused")).not.toBeNull();
    expect(container.querySelector(".hr-position-context-metrics")).toBeNull();
    expect(container.querySelector(".hr-position-sections")).toBeNull();
    expect(container.querySelector(".hr-position-taskbar")).toBeNull();
    expect(container.querySelector(".hr-position-quick-tasks")).toBeNull();
    expect(container.querySelector(".hr-task-recovery")).toBeNull();
    expect(container.textContent).toContain("3D 打印高级结构工程师");
    expect(container.textContent).toContain("研发 · 深圳 · 中山");
    expect(container.textContent).toContain("喷嘴与挤出工艺面试");
    expect(container.textContent).not.toContain("其他岗位候选人");
    expect(container.textContent).toContain("围绕这个岗位，直接开始协作");
    expect(container.textContent).not.toContain("当前没有执行中任务");
    expect(container.textContent).not.toContain("岗位智能工作台");
    expect(container.textContent).not.toContain("返回专业 Agent");
    expect([...container.querySelectorAll("button")].some((item) => item.textContent === "岗位资料")).toBe(true);
    expect([...container.querySelectorAll("button")].some((item) => item.textContent === "岗位任务")).toBe(true);

    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "保留这段岗位分析");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const fileInput = container.querySelector<HTMLInputElement>('.agent-direct-attachments input[type="file"]')!;
    Object.defineProperty(fileInput, "files", {
      configurable: true,
      value: [new File(["JD"], "待发送JD.pdf", { type: "application/pdf" })],
    });
    await act(async () => fileInput.dispatchEvent(new Event("change", { bubbles: true })));
    const queuedAttachment = container.querySelector(".conversation-upload-chip");
    expect(queuedAttachment?.textContent).toContain("待发送JD.pdf");
    await clickButton(container, "岗位资料");
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).not.toBeNull();
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(textarea.value).toBe("保留这段岗位分析");
    expect(container.querySelector(".conversation-upload-chip")).toBe(queuedAttachment);
    expect(container.textContent).toContain("J11014");
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="关闭岗位资料"]')?.click());
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).toBeNull();
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(textarea.value).toBe("保留这段岗位分析");
    expect(container.querySelector(".conversation-upload-chip")).toBe(queuedAttachment);
    await openTaskMenu(container);
    expect(container.querySelector('[role="menu"][aria-label="选择岗位任务"]')).not.toBeNull();
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(textarea.value).toBe("保留这段岗位分析");
    expect(container.querySelector(".conversation-upload-chip")).toBe(queuedAttachment);
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(container.querySelector('[role="menu"][aria-label="选择岗位任务"]')).toBeNull();
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(textarea.value).toBe("保留这段岗位分析");
    expect(container.querySelector(".conversation-upload-chip")).toBe(queuedAttachment);

    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="查看已归档对话"]')?.click());
    expect(container.textContent).toContain("历史岗位画像");
    expect(container.textContent).not.toContain("其他岗位候选人");
  });

  it("creates a new conversation inside the exact position scope", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    expect(document.activeElement).toBe(textarea);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "梳理面试方案");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>("button[type=submit]")?.click());

    expect(deps.createSubmission).toHaveBeenCalledWith(
      "梳理面试方案", "csrf", "hr-bot", { positionId: POSITION_ID },
    );
    expect(deps.onOpenConversation).toHaveBeenCalledWith(`/hr/positions/${POSITION_ID}/conversations/${ACTIVE_ID}`);
  });

  it("keeps the all-positions link inside the preview deployment prefix", async () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/hr/positions/example");
    const deps = dependencies();

    await act(async () => root.render(
      <HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />,
    ));

    expect(container.querySelector<HTMLAnchorElement>(".hr-position-bar a")?.getAttribute("href"))
      .toBe("/_preview/dingtalk-r1/hr/positions");
  });

  it("refuses a deep-linked conversation outside the position and keeps hard-stale workspaces read-only", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace
      account={{ ...account, hard_stale_read_only: true }} conversationId={OUTSIDER_ID}
      positionId={POSITION_ID} {...deps}
    />));

    expect(container.textContent).toContain("该对话不属于当前岗位");
    expect(container.querySelector<HTMLTextAreaElement>("#direct-agent-request")?.disabled).toBe(true);
    expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "岗位任务")?.disabled).toBe(true);
    expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "＋ 新对话")?.disabled).toBe(true);
  });

  it("keeps quick-task materials explicitly empty until HR selects this turn", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    await openTaskMenu(container);
    await clickButton(container, "生成岗位说明（JD）");
    expect(deps.r12Api.startTask).toHaveBeenLastCalledWith(POSITION_ID, "jd", expect.any(String), expect.objectContaining({ materialIds: [], conversationId: undefined }), expect.any(AbortSignal));
    await openTaskMenu(container);
    const material = container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')!;
    expect(material.checked).toBe(false);
    await act(async () => material.click());
    await clickButton(container, "梳理岗位要求（JR）");
    expect(deps.r12Api.startTask).toHaveBeenLastCalledWith(POSITION_ID, "jr", expect.any(String), expect.objectContaining({ materialIds: ["55555555-5555-4555-8555-555555555555"] }), expect.any(AbortSignal));
  });

  it("continues a validated current conversation for position quick tasks", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={account} conversationId={ACTIVE_ID} positionId={POSITION_ID} {...deps} />));
    await openTaskMenu(container);
    await clickButton(container, "生成岗位说明（JD）");
    expect(deps.r12Api.startTask).toHaveBeenLastCalledWith(POSITION_ID, "jd", expect.any(String), expect.objectContaining({ conversationId: ACTIVE_ID }), expect.any(AbortSignal));
  });

  it("restores durable active tasks while legacy section routes control the details drawer without replacing chat", async () => {
    const deps = dependencies(); deps.r12Api.activeTasks.mockResolvedValue([{ taskId: "durable-task", status: "running", taskKind: "talent_profile" }]);
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="chat" {...deps} />));
    expect(container.textContent).toContain("人才画像：执行中");
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request");
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).toBeNull();

    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="candidates" {...deps} />));
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).not.toBeNull();
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("候选人");

    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="artifacts" {...deps} />));
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("材料与成果");

    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="context" {...deps} />));
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("岗位信息");

    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="chat" {...deps} />));
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).toBeNull();
    expect(container.querySelector(".hr-position-sections")).toBeNull();
    expect(container.querySelector(".agent-use-workspace.is-focused")).not.toBeNull();
  });

  it("keeps context and material selection usable when task recovery is temporarily unavailable", async () => {
    const deps = dependencies(); deps.r12Api.activeTasks.mockRejectedValue({ status: 503 });
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    await openTaskMenu(container);
    expect(container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')).not.toBeNull();
    expect(container.textContent).toContain("任务状态暂时不可用");
    expect([...container.querySelectorAll<HTMLButtonElement>("button")].some((button) => button.textContent === "刷新任务状态")).toBe(true);
  });

  it("retains one idempotency key and selected materials after an uncertain failure, then clears on success", async () => {
    const deps = dependencies(); deps.r12Api.startTask.mockRejectedValueOnce({ status: 503 }).mockResolvedValueOnce({ taskId: "task", status: "accepted", taskKind: "jd" });
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    await openTaskMenu(container);
    let material = container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')!;
    await act(async () => material.click());
    await clickButton(container, "生成岗位说明（JD）");
    await openTaskMenu(container);
    material = container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')!;
    expect(material.checked).toBe(true);
    await clickButton(container, "生成岗位说明（JD）");
    expect(deps.r12Api.startTask.mock.calls[0]?.[2]).toBe(deps.r12Api.startTask.mock.calls[1]?.[2]);
    await openTaskMenu(container);
    material = container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')!;
    expect(material.checked).toBe(false);
  });

  it("retains selected materials when a resolved task start reports failed", async () => {
    const deps = dependencies();
    deps.r12Api.startTask.mockResolvedValue({ taskId: "task", status: "failed", taskKind: "jd", error: "dispatch failed" });
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    await openTaskMenu(container);
    let material = container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')!;
    await act(async () => material.click());
    await clickButton(container, "生成岗位说明（JD）");
    await openTaskMenu(container);
    material = container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')!;
    expect(material.checked).toBe(true);
    expect(container.textContent).toContain("执行失败");
  });

  it("resets per-turn materials when the current conversation changes", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={account} conversationId={ACTIVE_ID} positionId={POSITION_ID} {...deps} />));
    await openTaskMenu(container);
    await act(async () => container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')?.click());
    expect(container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')?.checked).toBe(true);
    await act(async () => root.render(<HrPositionWorkspace account={account} conversationId={ARCHIVED_ID} positionId={POSITION_ID} {...deps} />));
    await openTaskMenu(container);
    expect(container.querySelector<HTMLInputElement>('.hr-position-task-materials input[type="checkbox"]')?.checked).toBe(false);
  });

  it("polls durable active tasks, names their kind/status, and stops after completion", async () => {
    vi.useFakeTimers(); const deps = dependencies();
    deps.r12Api.activeTasks.mockReset().mockResolvedValueOnce([{ taskId: "durable-task", status: "running", taskKind: "talent_profile" }]).mockResolvedValueOnce([]);
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    expect(container.textContent).toContain("人才画像：执行中");
    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(container.textContent).not.toContain("当前没有执行中任务");
    expect(container.querySelector('[aria-label="岗位任务状态"]')).toBeNull();
    const calls = deps.r12Api.activeTasks.mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(20_000));
    expect(deps.r12Api.activeTasks).toHaveBeenCalledTimes(calls);
  });

  it("clears stale task status while a replacement recovery source is loading", async () => {
    const deps = dependencies();
    deps.r12Api.activeTasks.mockResolvedValue([{ taskId: "old-task", status: "running", taskKind: "jd" }]);
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    expect(container.textContent).toContain("JD：执行中");

    const replacement = { ...deps.r12Api, activeTasks: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} r12Api={replacement as never} />));

    expect(container.querySelector('[aria-label="岗位任务状态"]')).toBeNull();
    expect(container.textContent).not.toContain("JD：执行中");
  });

  it("drops stale active tasks when polling recovery fails", async () => {
    vi.useFakeTimers();
    const deps = dependencies();
    deps.r12Api.activeTasks.mockReset()
      .mockResolvedValueOnce([{ taskId: "old-task", status: "running", taskKind: "jd" }])
      .mockRejectedValueOnce(new Error("offline"));
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    expect(container.textContent).toContain("JD：执行中");

    await act(async () => vi.advanceTimersByTimeAsync(2_000));

    expect(container.textContent).toContain("任务状态暂时不可用");
    expect(container.textContent).not.toContain("JD：执行中");
  });

  it("refreshes resources when one durable generation task completes while another remains active", async () => {
    vi.useFakeTimers(); const deps = dependencies();
    deps.api.position.mockReset()
      .mockResolvedValueOnce(detail)
      .mockResolvedValue({ ...detail, artifactCount: 4, artifactAttachmentIds: ["88888888-8888-4888-8888-888888888888"] });
    deps.r12Api.activeTasks.mockReset().mockResolvedValueOnce([
      { taskId: "durable-task", status: "running", taskKind: "jd" },
      { taskId: "other-task", status: "running", taskKind: "jr" },
    ]).mockResolvedValueOnce([{ taskId: "other-task", status: "running", taskKind: "jr" }]);
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="artifacts" {...deps} />));
    const beforeCompletion = deps.r12Api.resources.mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(deps.api.position).toHaveBeenCalledTimes(2);
    expect((deps.api.position.mock.calls[1][1] as AbortSignal).aborted).toBe(false);
    await clickButton(container, "岗位资料");
    await clickButton(container, "材料与成果");
    expect(deps.r12Api.resources.mock.calls.length).toBeGreaterThan(beforeCompletion);
    expect(container.textContent).toContain("岗位材料与成果");
  });

  it("refreshes a mounted context panel after durable result projection completes", async () => {
    vi.useFakeTimers(); const deps = dependencies();
    deps.r12Api.activeTasks.mockReset().mockResolvedValueOnce([
      { taskId: "durable-task", status: "running", taskKind: "talent_profile" },
    ]).mockResolvedValueOnce([]);
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="context" {...deps} />));
    await clickButton(container, "岗位资料");
    const beforeCompletion = deps.r12Api.context.mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(deps.r12Api.context.mock.calls.length).toBeGreaterThan(beforeCompletion);
  });

  it("updates the parent context immediately after human confirmation", async () => {
    const deps = dependencies();
    const draft = { contextVersionId: "66666666-6666-4666-8666-666666666666", positionId: POSITION_ID, displayVersion: 1, status: "draft", summary: "画像草稿", modules: { talent_profile: { summary: "高级工程师" } }, officialVersionId: null, baseContextVersionId: null, sourceConversationId: null, sourceTurnId: null, sourceArtifactVersionId: null, sourceMaterialAttachmentIds: [], agentId: null, modelVersion: null, rowVersion: 1, createdAt: "2026-09-04T00:00:00Z", confirmedAt: null };
    const confirmed = { ...draft, contextVersionId: "77777777-7777-4777-8777-777777777777", status: "confirmed", confirmedAt: "2026-09-04T00:01:00Z" };
    deps.r12Api.context.mockResolvedValue({ current: null, drafts: [draft], history: [draft] });
    deps.r12Api.confirmContext.mockResolvedValue(confirmed);
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="context" {...deps} />));
    await clickButton(container, "岗位资料");
    await act(async () => container.querySelector<HTMLInputElement>('.hr-context-panel input[type="checkbox"]')?.click());
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "确认选中模块")?.click());
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="关闭岗位资料"]')?.click());
    await openTaskMenu(container);
    await clickButton(container, "生成岗位说明（JD）");
    expect(deps.r12Api.startTask).toHaveBeenLastCalledWith(POSITION_ID, "jd", expect.any(String), expect.objectContaining({ contextVersionId: confirmed.contextVersionId }), expect.any(AbortSignal));
  });

  it("keeps the conversation mounted while business details open on demand", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} section="candidates" {...deps} />));
    expect(container.querySelector('[aria-label="对话列表面板"]')).not.toBeNull();
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    expect(textarea).not.toBeNull();
    expect(container.querySelector('section[aria-label="批量简历导入"]')).not.toBeNull();
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
  });

  it("keeps the exact composer mounted while opening and closing the accessible details drawer", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={account} positionId={POSITION_ID} {...deps} />));
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    const opener = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "岗位资料")!;
    await act(async () => { opener.focus(); opener.click(); });
    const dialog = container.querySelector<HTMLElement>('[role="dialog"][aria-label="岗位资料"]')!;
    expect(dialog).not.toBeNull();
    expect(document.activeElement?.getAttribute("aria-label")).toBe("关闭岗位资料");
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
    await act(async () => dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.activeElement).toBe(opener);
    expect(container.querySelector("#direct-agent-request")).toBe(textarea);
  });

  it("blocks quick tasks and candidate/context writes in hard-stale mode", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={{ ...account, hard_stale_read_only: true }} positionId={POSITION_ID} section="candidates" {...deps} />));
    expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "岗位任务")?.disabled).toBe(true);
    await clickButton(container, "岗位资料");
    await clickButton(container, "候选人");
    expect(container.querySelector<HTMLInputElement>('section[aria-label="批量简历导入"] input[type="file"]')?.disabled).toBe(true);
    expect(deps.r12Api.startTask).not.toHaveBeenCalled();
  });

  it("blocks resource ticket writes in hard-stale mode", async () => {
    const deps = dependencies();
    await act(async () => root.render(<HrPositionWorkspace account={{ ...account, hard_stale_read_only: true }} positionId={POSITION_ID} section="artifacts" {...deps} />));
    await clickButton(container, "岗位资料");
    await clickButton(container, "材料与成果");
    const resourceButtons = [...container.querySelectorAll<HTMLButtonElement>(".hr-resource-actions button")];
    const resourceCheckboxes = [...container.querySelectorAll<HTMLInputElement>('.hr-resource-actions input[type="checkbox"]')];
    expect(resourceButtons.length).toBeGreaterThan(0);
    expect(resourceCheckboxes.length).toBeGreaterThan(0);
    expect(resourceButtons.every((button) => button.disabled)).toBe(true);
    expect(resourceCheckboxes.every((input) => input.disabled)).toBe(true);
    expect(deps.r12Api.downloadResource).not.toHaveBeenCalled();
  });
});
