/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { fetchAgentCatalog } from "../../brainApi";
import type { AgentCapabilityCard } from "../../brainTypes";
import { listConversationAttachments } from "../../attachmentApi";
import {
  fetchConversation, fetchConversationMessages, listConversations,
  startConversation, streamConversationEvents,
} from "../../conversationApi";
import { createHrApi } from "../../hrApi";
import { createHrR12Api } from "../../hrR12Api";
import { HrWorkspacePage } from "./HrWorkspacePage";


vi.mock("../../brainApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../brainApi")>(),
  fetchAgentCatalog: vi.fn(),
}));

vi.mock("../../conversationApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../conversationApi")>(),
  fetchConversation: vi.fn(),
  fetchConversationMessages: vi.fn(),
  listConversations: vi.fn(),
  startConversation: vi.fn(),
  streamConversationEvents: vi.fn(),
}));

vi.mock("../../hrApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../hrApi")>(),
  createHrApi: vi.fn(),
}));

vi.mock("../../hrR12Api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../hrR12Api")>(),
  createHrR12Api: vi.fn(),
}));

vi.mock("./HrPositionWorkspace", async (importOriginal) => ({
  ...await importOriginal<typeof import("./HrPositionWorkspace")>(),
  HrPositionWorkspace: () => <div className="agent-use-workspace" data-agent-id="hr-bot" />,
}));

vi.mock("./HrPanoramaWorkspace", () => ({
  HrPanoramaWorkspace: () => <div data-panorama-workspace>全景报告</div>,
}));

vi.mock("../../attachmentApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../attachmentApi")>();
  const upload = {
    uploadId: "upload-free-chat", attachmentId: "attachment-free-chat", conversationId: null,
    displayName: "候选人简历.pdf", declaredMime: "application/pdf", declaredSize: 6,
    state: "uploading" as const, uploadedBytes: 6, expiresAt: "2026-09-04T12:00:00Z",
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
    listConversationAttachments: vi.fn().mockResolvedValue([]),
    cancelAttachmentUpload: vi.fn().mockResolvedValue(undefined),
  };
});


const account: Account = {
  internal_user_id: "member",
  display_name: "磐德",
  role: "member",
  departments: [],
  gender: null,
  observation_agent_ids: [],
  workspace_scopes: [],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf",
};

const hrCard: AgentCapabilityCard = {
  agent_id: "hr-bot",
  display_name: "HR Agent",
  domain_group: "HR",
  persona_subtitle: "Hannah · 技术人才搜寻与招聘协作",
  mission: "帮助员工和管理者完成招聘、人事与员工服务任务。",
  capabilities: ["梳理岗位需求与候选人画像"],
  exclusions: ["不代替管理者作出录用决定"],
  example_tasks: ["根据岗位说明梳理候选人能力组合"],
  required_inputs: ["任务目标"],
  accepted_input_types: ["text"],
  output_types: ["text"],
  supports_attachments_in: false,
  supports_attachments_out: false,
  attachment_limits: null,
  supports_evidence: true,
  supports_streaming: true,
  supports_cancellation: true,
  supports_idempotency: true,
  max_duration_seconds: 300,
  data_classification: "internal",
  adapter_id: "metabot-core-chat",
  capability_version: 1,
  adapter_kind: "metabot_local",
  adapter_config_version: 1,
  output_contract: "normalized_task_result_v1",
  interaction_modes: ["direct_chat", "brain_delegation"],
  workspace_url: null,
};

const positionId = "44444444-4444-4444-8444-444444444444";
const positionPackage = {
  draftId: "11111111-1111-4111-8111-111111111111",
  draftVersionId: "22222222-2222-4222-8222-222222222222",
  conversationId: "c-7",
  versionNumber: 2,
  title: "视觉算法工程师",
  modules: {
    mission: { text: "负责空间视觉算法落地" },
    jd: { text: "对外 JD" },
    jr: { text: "内部 JR" },
  },
  rowVersion: 3,
  createdAt: "2026-09-04T01:00:00Z",
  updatedAt: "2026-09-04T02:00:00Z",
};


describe("HrWorkspacePage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.mocked(fetchAgentCatalog).mockResolvedValue([hrCard]);
    vi.mocked(listConversations).mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(listConversationAttachments).mockResolvedValue([]);
    vi.mocked(fetchConversation).mockResolvedValue({
      conversation: {
        conversation_id: "c-7", mode: "direct_agent", direct_agent_id: "hr-bot",
        title: "招聘对话", status: "active", summary_through_seq: 0,
        created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:00Z", archived_at: null,
      },
      current_turn: null,
    });
    vi.mocked(fetchConversationMessages).mockResolvedValue([{
      message_id: "message-1", conversation_id: "c-7", seq: 1, role: "assistant",
      content: "此前对话消息", turn_id: null, delivery_status: "completed",
      created_at: "2026-09-04T00:00:00Z", completed_at: "2026-09-04T00:00:01Z",
      input_attachments: [], output_attachments: [], active_attachment_ids: [],
    }]);
    vi.mocked(streamConversationEvents).mockResolvedValue(undefined);
    vi.mocked(createHrApi).mockReturnValue({
      listPositions: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
      listDrafts: vi.fn().mockResolvedValue([]),
      positionPackage: vi.fn().mockRejectedValue({ status: 404 }),
      confirmPositionPackage: vi.fn(),
      position: vi.fn().mockResolvedValue({
        positionId: "44444444-4444-4444-8444-444444444444", sourceKind: "manual",
        officialJobId: null, title: "视觉算法工程师", department: "研发", locations: ["深圳"],
        officialStatus: null, internalStatus: "active", sourceVersion: null, rowVersion: 1,
        createdAt: "2026-09-04T00:00:00Z", updatedAt: "2026-09-04T00:00:00Z",
        conversationCount: 1, materialCount: 0, artifactCount: 0,
        conversationIds: ["c-7"], materialAttachmentIds: [], artifactIds: [], artifactAttachmentIds: [],
      }),
    } as never);
    vi.mocked(createHrR12Api).mockReturnValue({
      context: vi.fn().mockResolvedValue({ current: null, drafts: [], history: [] }),
      compareContext: vi.fn(), confirmContext: vi.fn(),
      candidateDrafts: vi.fn().mockResolvedValue([]), positionCandidates: vi.fn().mockResolvedValue([]),
      candidate: vi.fn(), candidateDocuments: vi.fn(), candidateAnalyses: vi.fn(), candidateFeedback: vi.fn(),
      retryDraft: vi.fn(), confirmDraft: vi.fn(), createCandidateDraftBatch: vi.fn(),
      appendCandidateFeedback: vi.fn(), compareCandidates: vi.fn(), downloadCandidateDocument: vi.fn(),
      startTask: vi.fn(), taskStatus: vi.fn(),
      resources: vi.fn().mockResolvedValue({ materials: [], artifacts: [] }), downloadResource: vi.fn(),
    } as never);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it("opens a conversation-first HR workspace at the canonical root", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} />));

    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).not.toBeNull();
    expect(container.textContent).not.toContain("官网岗位");
    expect(container.querySelector(".agent-task-starter")).toBeNull();
    expect(listConversations).toHaveBeenCalled();
  });

  it("opens existing position data only on the positions route", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} positions />));

    expect(container.textContent).toContain("官网岗位");
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).not.toBeNull();
    expect(container.querySelector<HTMLElement>(".hr-workspace-chat-panel")?.hidden).toBe(true);
    expect(createHrApi).toHaveBeenCalled();
    expect(listConversations).toHaveBeenCalled();
  });

  it("keeps an unsent chat draft mounted while visiting positions", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    const textarea = container.querySelector<HTMLTextAreaElement>("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "不要丢失的岗位需求");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => root.render(<HrWorkspacePage account={account} positions />));
    expect(container.querySelector<HTMLTextAreaElement>(".hr-workspace-chat-panel textarea")?.value)
      .toBe("不要丢失的岗位需求");

    await act(async () => root.render(<HrWorkspacePage account={account} />));
    expect(container.querySelector<HTMLTextAreaElement>(".hr-workspace-chat-panel textarea")?.value)
      .toBe("不要丢失的岗位需求");
  });

  it("keeps the same chat host, unsent text, selected upload, and current conversation while visiting panorama", async () => {
    vi.mocked(fetchAgentCatalog).mockResolvedValue([{ ...hrCard,
      accepted_input_types: ["text", "image", "pdf", "office"], supports_attachments_in: true,
      attachment_limits: { max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 5, max_bytes_per_message: 50 * 1024 * 1024, max_files_per_conversation: 50, max_bytes_per_conversation: 500 * 1024 * 1024 },
    }]);
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    const workspace = container.querySelector<HTMLElement>('.agent-use-workspace[data-agent-id="hr-bot"]')!;
    const textarea = container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "不应丢失的草稿"); textarea.dispatchEvent(new Event("input", { bubbles: true })); });
    const input = container.querySelector<HTMLInputElement>('.conversation-composer-attachments input[type="file"]')!;
    Object.defineProperty(input, "files", { configurable: true, value: [new File(["resume"], "待发送简历.pdf", { type: "application/pdf" })] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    expect(container.querySelector("[data-panorama-workspace]")).not.toBeNull();
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(workspace);
    expect(container.querySelector<HTMLElement>(".hr-workspace-chat-panel")?.hidden).toBe(true);
    expect(textarea.value).toBe("不应丢失的草稿");
    expect(container.textContent).toContain("待发送简历.pdf");
    expect(container.querySelector<HTMLAnchorElement>('.hr-workspace-nav a[href="/hr/conversations/c-7"]')).not.toBeNull();

    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(workspace);
    expect(container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")?.value).toBe("不应丢失的草稿");
    expect(container.textContent).toContain("待发送简历.pdf");
  });

  it("mounts only the position conversation workspace on a position detail route", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} positionId="position-7" />));

    expect(container.querySelector(".hr-workspace-chat-panel")).toBeNull();
    expect(container.querySelectorAll('.agent-use-workspace[data-agent-id="hr-bot"]')).toHaveLength(1);
  });

  it("restores the current user's free-chat text and ready upload queue after a position detail visit", async () => {
    vi.mocked(fetchAgentCatalog).mockResolvedValue([{ ...hrCard,
      accepted_input_types: ["text", "image", "pdf", "office"], supports_attachments_in: true,
      attachment_limits: {
        max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 5,
        max_bytes_per_message: 50 * 1024 * 1024, max_files_per_conversation: 50,
        max_bytes_per_conversation: 500 * 1024 * 1024,
      },
    }]);
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "保留自由聊天草稿");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const fileInput = container.querySelector<HTMLInputElement>('.agent-direct-attachments input[type="file"]')!;
    Object.defineProperty(fileInput, "files", {
      configurable: true,
      value: [new File(["resume"], "候选人简历.pdf", { type: "application/pdf" })],
    });
    await act(async () => fileInput.dispatchEvent(new Event("change", { bubbles: true })));
    expect(container.querySelector('.conversation-upload-chip[data-state="ready"]')?.textContent).toContain("候选人简历.pdf");

    await act(async () => root.render(<HrWorkspacePage account={account} positionId="position-7" />));
    expect(container.querySelectorAll('.agent-use-workspace[data-agent-id="hr-bot"]')).toHaveLength(1);
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    expect(container.querySelector<HTMLTextAreaElement>("#direct-agent-request")?.value).toBe("保留自由聊天草稿");
    expect(container.querySelector('.conversation-upload-chip[data-state="ready"]')?.textContent).toContain("候选人简历.pdf");
    expect(container.querySelector(".conversation-attachment-card")?.textContent).toContain("候选人简历.pdf");

    await act(async () => root.render(<HrWorkspacePage account={{ ...account, internal_user_id: "other-user" }} />));
    expect(container.querySelector<HTMLTextAreaElement>("#direct-agent-request")?.value).toBe("");
    expect(container.querySelector(".conversation-upload-chip")).toBeNull();
  });

  it("does not restore or submit a ready attachment removed before a position detail visit", async () => {
    vi.mocked(fetchAgentCatalog).mockResolvedValue([{ ...hrCard,
      accepted_input_types: ["text", "image", "pdf", "office"], supports_attachments_in: true,
      attachment_limits: {
        max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 5,
        max_bytes_per_message: 50 * 1024 * 1024, max_files_per_conversation: 50,
        max_bytes_per_conversation: 500 * 1024 * 1024,
      },
    }]);
    vi.mocked(startConversation).mockReturnValue({
      idempotencyKey: "removed-ready-attachment",
      send: vi.fn().mockRejectedValue(new Error("stop after submission capture")),
    });
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    const fileInput = container.querySelector<HTMLInputElement>('.agent-direct-attachments input[type="file"]')!;
    Object.defineProperty(fileInput, "files", {
      configurable: true,
      value: [new File(["resume"], "候选人简历.pdf", { type: "application/pdf" })],
    });
    await act(async () => fileInput.dispatchEvent(new Event("change", { bubbles: true })));
    expect(container.querySelector('.conversation-upload-chip[data-state="ready"]')).not.toBeNull();

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "移除")?.click());
    await act(async () => root.render(<HrWorkspacePage account={account} positionId="position-7" />));
    await act(async () => root.render(<HrWorkspacePage account={account} />));

    expect(container.querySelector(".conversation-upload-chip")).toBeNull();
    expect(container.querySelector(".conversation-attachment-card")).toBeNull();
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "继续招聘工作");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".agent-direct-submit")?.click());
    expect(startConversation).toHaveBeenCalledWith({
      text: "继续招聘工作", attachmentIds: [], activeAttachmentIds: [],
    }, "csrf", "hr-bot");
  });

  it("keeps the current conversation as the chat navigation target", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    await act(async () => root.render(<HrWorkspacePage account={account} positions />));

    expect(container.querySelector<HTMLAnchorElement>(
      '.hr-workspace-nav a[href="/hr/conversations/c-7"]',
    )?.textContent).toBe("对话");
  });

  it("opens a new HR conversation at the canonical workspace root with a trailing slash", async () => {
    window.history.replaceState({}, "", "/hr/conversations/c-1");
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-1" />));

    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-sidebar-new")?.click());

    expect(window.location.pathname).toBe("/hr/");
  });

  it("keeps the same conversation host, messages, and composer when confirmation routes into its position", async () => {
    const conversationId = "c-7";
    const positionId = "44444444-4444-4444-8444-444444444444";
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId={conversationId} />));
    const workspace = container.querySelector<HTMLElement>('.agent-use-workspace[data-agent-id="hr-bot"]')!;
    const composer = container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(composer, "未发送的补充要求");
      composer.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.textContent).toContain("此前对话消息");

    const firstR12Client = vi.mocked(createHrR12Api).mock.results[0]?.value as {
      context: ReturnType<typeof vi.fn>;
    };
    firstR12Client.context.mockRejectedValueOnce(new Error("context offline"));

    await act(async () => root.render(<HrWorkspacePage
      account={account} conversationId={conversationId} positionId={positionId}
    />));

    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(workspace);
    expect(container.textContent).toContain("此前对话消息");
    expect(container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")).toBe(composer);
    expect(composer.value).toBe("未发送的补充要求");
    expect(container.querySelectorAll('.agent-use-workspace[data-agent-id="hr-bot"]')).toHaveLength(1);

    const details = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "岗位资料")!;
    expect(details).toBeDefined();
    await act(async () => details.click());
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).not.toBeNull();
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(workspace);
  });

  it("keeps a trusted confirmed thread visible while position validation is pending", async () => {
    vi.mocked(fetchAgentCatalog).mockResolvedValue([{ ...hrCard,
      accepted_input_types: ["text", "image", "pdf", "office"], supports_attachments_in: true,
      attachment_limits: {
        max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 5,
        max_bytes_per_message: 50 * 1024 * 1024, max_files_per_conversation: 50,
        max_bytes_per_conversation: 500 * 1024 * 1024,
      },
    }]);
    vi.mocked(listConversationAttachments).mockResolvedValue([{
      attachmentId: "attachment-ready", conversationId: "c-7", source: "user", displayName: "岗位访谈.pdf",
      detectedMime: "application/pdf", sizeBytes: 6, sha256: null, state: "ready", stateReason: null,
      createdAt: "2026-09-04T00:00:00Z", retainedUntil: "2027-09-04T00:00:00Z", preview: null, coverage: null,
    }]);
    vi.mocked(fetchConversation).mockResolvedValue({
      conversation: {
        conversation_id: "c-7", mode: "direct_agent", direct_agent_id: "hr-bot", title: "招聘对话",
        status: "active", summary_through_seq: 0, created_at: "2026-09-04T00:00:00Z",
        updated_at: "2026-09-04T00:00:00Z", archived_at: null,
      },
      current_turn: {
        turn_id: "turn-active", conversation_id: "c-7", user_message_id: "message-user",
        assistant_message_id: null, retry_of_turn_id: null, status: "running",
        created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:01Z",
      },
    });
    let streamSignal: AbortSignal | undefined;
    vi.mocked(streamConversationEvents).mockImplementation((_id, options) => {
      streamSignal = options.signal;
      return new Promise(() => undefined);
    });
    const client = vi.mocked(createHrApi)("csrf") as unknown as {
      position: ReturnType<typeof vi.fn>;
      positionPackage: ReturnType<typeof vi.fn>;
      confirmPositionPackage: ReturnType<typeof vi.fn>;
    };
    client.positionPackage.mockResolvedValue(positionPackage);
    client.confirmPositionPackage.mockResolvedValue({ positionId, conversationId: "c-7", contextVersionId: "context-1" });
    client.position.mockReturnValueOnce(new Promise(() => undefined));

    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    const workspace = container.querySelector<HTMLElement>('.agent-use-workspace[data-agent-id="hr-bot"]')!;
    const composer = container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(composer, "确认后继续补充");
      composer.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认并加入岗位库")?.click());
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" positionId={positionId} />));

    expect(container.querySelector<HTMLElement>(".hr-workspace-chat-panel")?.hidden).toBe(false);
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(workspace);
    expect(container.textContent).toContain("此前对话消息");
    expect(container.textContent).toContain("岗位访谈.pdf");
    expect(container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")).toBe(composer);
    expect(composer.value).toBe("确认后继续补充");
    expect(streamSignal?.aborted).toBe(false);
  });

  it("keeps a trusted confirmed thread visible when position details fail", async () => {
    const client = vi.mocked(createHrApi)("csrf") as unknown as {
      position: ReturnType<typeof vi.fn>;
      positionPackage: ReturnType<typeof vi.fn>;
      confirmPositionPackage: ReturnType<typeof vi.fn>;
    };
    client.positionPackage.mockResolvedValue(positionPackage);
    client.confirmPositionPackage.mockResolvedValue({ positionId, conversationId: "c-7", contextVersionId: "context-1" });
    client.position.mockRejectedValueOnce(new Error("offline"));

    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    const workspace = container.querySelector<HTMLElement>('.agent-use-workspace[data-agent-id="hr-bot"]')!;
    const composer = container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")!;
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认并加入岗位库")?.click());
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" positionId={positionId} />));

    expect(container.querySelector<HTMLElement>(".hr-workspace-chat-panel")?.hidden).toBe(false);
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(workspace);
    expect(container.textContent).toContain("此前对话消息");
    expect(container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")).toBe(composer);
    expect([...container.querySelectorAll<HTMLButtonElement>("button")]
      .some((button) => button.textContent === "重新读取岗位资料")).toBe(true);
    const details = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "岗位资料")!;
    await act(async () => details.click());
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).not.toBeNull();
    expect(container.querySelector('[role="dialog"]')?.textContent).toContain("岗位资料暂时无法完整读取");
  });

  it("uses the stable conversation host on a fresh canonical position-conversation route", async () => {
    await act(async () => root.render(<HrWorkspacePage
      account={account} conversationId="c-7" positionId="44444444-4444-4444-8444-444444444444"
    />));

    expect(container.textContent).toContain("此前对话消息");
    expect(container.querySelector(".conversation-composer textarea")).not.toBeNull();
    expect(container.querySelectorAll('.agent-use-workspace[data-agent-id="hr-bot"]')).toHaveLength(1);
  });

  it("validates a fresh canonical route before loading chat and scopes history to the position", async () => {
    let resolveDetail: ((detail: unknown) => void) | undefined;
    const client = vi.mocked(createHrApi)("csrf") as unknown as {
      position: ReturnType<typeof vi.fn>;
    };
    client.position.mockReturnValueOnce(new Promise((resolve) => { resolveDetail = resolve; }));
    vi.mocked(listConversations).mockResolvedValue({ items: [{
      conversation_id: "c-7", mode: "direct_agent", direct_agent_id: "hr-bot", title: "Position A conversation",
      status: "active", summary_through_seq: 0, created_at: "2026-09-04T00:00:00Z",
      updated_at: "2026-09-04T00:00:00Z", archived_at: null,
    }, {
      conversation_id: "c-b", mode: "direct_agent", direct_agent_id: "hr-bot", title: "Position B conversation",
      status: "active", summary_through_seq: 0, created_at: "2026-09-04T00:00:00Z",
      updated_at: "2026-09-04T00:00:00Z", archived_at: null,
    }], next_cursor: null });
    vi.mocked(fetchConversation).mockResolvedValueOnce({
      conversation: {
        conversation_id: "c-7", mode: "direct_agent", direct_agent_id: "hr-bot", title: "Position A conversation",
        status: "active", summary_through_seq: 0, created_at: "2026-09-04T00:00:00Z",
        updated_at: "2026-09-04T00:00:00Z", archived_at: null,
      },
      current_turn: null,
    });

    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" positionId={positionId} />));
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBeNull();
    expect(fetchConversation).not.toHaveBeenCalled();

    await act(async () => resolveDetail?.({
      positionId, sourceKind: "manual", officialJobId: null, title: "Position A", department: "研发",
      locations: ["深圳"], officialStatus: null, internalStatus: "active", sourceVersion: null, rowVersion: 1,
      createdAt: "2026-09-04T00:00:00Z", updatedAt: "2026-09-04T00:00:00Z", conversationCount: 1,
      materialCount: 0, artifactCount: 0, conversationIds: ["c-7"], materialAttachmentIds: [],
      artifactIds: [], artifactAttachmentIds: [],
    }));

    expect(container.textContent).toContain("此前对话消息");
    expect(container.textContent).toContain("Position A conversation");
    expect(container.textContent).not.toContain("Position B conversation");
    expect(container.querySelector<HTMLAnchorElement>(`.conversation-session-link[href="/hr/positions/${positionId}/conversations/c-7"]`)).not.toBeNull();
    expect(container.querySelector<HTMLAnchorElement>(`.conversation-session-link[href="/hr/positions/${positionId}/conversations/c-b"]`)).toBeNull();
  });

  it("safely rejects a conversation that is not owned by the routed position", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-b" positionId={positionId} />));

    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBeNull();
    expect(fetchConversation).not.toHaveBeenCalledWith("c-b", expect.anything());
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("不属于这个岗位");
    expect(container.querySelector<HTMLAnchorElement>(`a[href="/hr/positions/${positionId}"]`)).not.toBeNull();
  });

  it("restores the confirmed terminal action after a fresh route remount", async () => {
    const client = vi.mocked(createHrApi)("csrf") as unknown as {
      positionPackage: ReturnType<typeof vi.fn>;
      confirmPositionPackage: ReturnType<typeof vi.fn>;
    };
    client.positionPackage.mockResolvedValue(positionPackage);

    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" positionId={positionId} />));
    const confirmed = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "已加入岗位库");
    expect(confirmed?.disabled).toBe(true);
    expect(client.confirmPositionPackage).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" positionId={positionId} />));
    expect([...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "已加入岗位库")?.disabled).toBe(true);
    expect(client.confirmPositionPackage).not.toHaveBeenCalled();
  });

  it("preserves ready attachments and an active stream across the same-conversation position route", async () => {
    vi.mocked(fetchAgentCatalog).mockResolvedValue([{ ...hrCard,
      accepted_input_types: ["text", "image", "pdf", "office"], supports_attachments_in: true,
      attachment_limits: {
        max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 5,
        max_bytes_per_message: 50 * 1024 * 1024, max_files_per_conversation: 50,
        max_bytes_per_conversation: 500 * 1024 * 1024,
      },
    }]);
    vi.mocked(listConversationAttachments).mockResolvedValue([{
      attachmentId: "attachment-ready", conversationId: "c-7", source: "user",
      displayName: "岗位访谈.pdf", detectedMime: "application/pdf", sizeBytes: 6, sha256: null,
      state: "ready", stateReason: null, createdAt: "2026-09-04T00:00:00Z",
      retainedUntil: "2027-09-04T00:00:00Z", preview: null, coverage: null,
    }]);
    vi.mocked(fetchConversation).mockResolvedValue({
      conversation: {
        conversation_id: "c-7", mode: "direct_agent", direct_agent_id: "hr-bot",
        title: "招聘对话", status: "active", summary_through_seq: 0,
        created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:00Z", archived_at: null,
      },
      current_turn: {
        turn_id: "turn-active", conversation_id: "c-7", user_message_id: "message-user",
        assistant_message_id: null, retry_of_turn_id: null, status: "running",
        created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:01Z",
      },
    });
    let streamSignal: AbortSignal | undefined;
    vi.mocked(streamConversationEvents).mockImplementation((_id, options) => {
      streamSignal = options.signal;
      return new Promise(() => undefined);
    });
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    expect(container.textContent).toContain("岗位访谈.pdf");
    expect(streamSignal?.aborted).toBe(false);

    await act(async () => root.render(<HrWorkspacePage
      account={account} conversationId="c-7" positionId="44444444-4444-4444-8444-444444444444"
    />));

    expect(streamSignal?.aborted).toBe(false);
    expect(listConversationAttachments).toHaveBeenCalledTimes(1);
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    expect(container.textContent).toContain("岗位访谈.pdf");
    expect(streamSignal?.aborted).toBe(false);
    expect(listConversationAttachments).toHaveBeenCalledTimes(1);
  });

  it("opens fallback package details while validation is unavailable and retries safely", async () => {
    const firstClient = vi.mocked(createHrApi)("csrf") as unknown as {
      position: ReturnType<typeof vi.fn>;
      positionPackage: ReturnType<typeof vi.fn>;
    };
    firstClient.position.mockRejectedValueOnce(new Error("offline"));
    firstClient.positionPackage.mockResolvedValue(positionPackage);
    const r12Client = vi.mocked(createHrR12Api)("csrf") as unknown as { context: ReturnType<typeof vi.fn> };
    r12Client.context.mockRejectedValueOnce(new Error("context offline"));
    await act(async () => root.render(<HrWorkspacePage
      account={account} conversationId="c-7" positionId={positionId}
    />));

    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBeNull();
    expect(container.textContent).toContain("视觉算法工程师");
    const details = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "岗位资料")!;
    expect(details?.disabled).toBe(false);
    await act(async () => details.click());
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).not.toBeNull();
    expect(container.querySelector('[role="dialog"]')?.textContent).toContain("岗位资料暂时无法完整读取");

    const retry = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "重新读取岗位资料")!;
    expect(retry).toBeDefined();
    await act(async () => retry.click());
    expect(container.textContent).toContain("此前对话消息");
    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).not.toBeNull();
  });
});
