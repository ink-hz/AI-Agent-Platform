/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { fetchAgentCatalog } from "../../brainApi";
import type { AgentCapabilityCard } from "../../brainTypes";
import { listConversations, startConversation } from "../../conversationApi";
import { createHrApi } from "../../hrApi";
import { HrWorkspacePage } from "./HrWorkspacePage";


vi.mock("../../brainApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../brainApi")>(),
  fetchAgentCatalog: vi.fn(),
}));

vi.mock("../../conversationApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../conversationApi")>(),
  listConversations: vi.fn(),
  startConversation: vi.fn(),
}));

vi.mock("../../hrApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../hrApi")>(),
  createHrApi: vi.fn(),
}));

vi.mock("./HrPositionWorkspace", () => ({
  HrPositionWorkspace: () => <div className="agent-use-workspace" data-agent-id="hr-bot" />,
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


describe("HrWorkspacePage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.mocked(fetchAgentCatalog).mockResolvedValue([hrCard]);
    vi.mocked(listConversations).mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(createHrApi).mockReturnValue({
      listPositions: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
      listDrafts: vi.fn().mockResolvedValue([]),
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
});
