/** @vitest-environment jsdom */

import { act, useState } from "react";
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
import type { HrIntelligenceReference } from "./hrIntelligenceReference";


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


vi.mock("./HrPanoramaWorkspace", () => ({
  HrPanoramaWorkspace: ({ onSelectReference }: { onSelectReference?: (reference: HrIntelligenceReference) => void }) => {
    const [search, setSearch] = useState("");
    return <div data-panorama-workspace>
    全景报告
    <input aria-label="模拟公司搜索" onChange={(event) => setSearch(event.target.value)} value={search} />
    <button onClick={() => onSelectReference?.({
      key: "fact:bundle-7:unit-2:fact-9", bundleId: "bundle-7", companyKey: "acme",
      companyName: "Acme Robotics", label: "海外岗位增长", generatedAt: "2026-09-08T06:00:00Z",
      excerpt: "招聘岗位主要分布于深圳。", sourceUrls: ["https://example.com/jobs/9"],
      unitId: "unit-2", claimType: "fact", localId: "fact-9",
    })} type="button">带入对话</button>
    <button onClick={() => onSelectReference?.({
      key: "fact:bundle-7:unit-2:fact-10", bundleId: "bundle-7", companyKey: "acme",
      companyName: "Acme Robotics", label: "新增情报", generatedAt: "2026-09-08T06:00:00Z",
      excerpt: "发送期间新增的选择。", sourceUrls: ["https://example.com/jobs/10"],
      unitId: "unit-2", claimType: "fact", localId: "fact-10",
    })} type="button">带入第二条</button>
  </div>;
  },
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

  it("opens JD and JR immediately after selecting a position while ancillary data is pending", async () => {
    history.replaceState({}, "", "/hr/");
    const client = vi.mocked(createHrApi).getMockImplementation()!('csrf');
    const r12 = vi.mocked(createHrR12Api).getMockImplementation()!('csrf');
    const position = { positionId, sourceKind: 'official_site', officialJobId: 'J10001', title: '视觉算法工程师', department: '研发', locations: ['深圳'], officialStatus: 'active', internalStatus: 'active', sourceVersion: 'v1', rowVersion: 1, createdAt: '2026-09-09T00:00:00Z', updatedAt: '2026-09-09T00:00:00Z' };
    vi.mocked(client.listPositions).mockResolvedValue({ items: [position], nextCursor: null } as never);
    vi.mocked(client.position).mockReturnValue(new Promise(() => {}));
    vi.mocked(r12.resources).mockReturnValue(new Promise(() => {}));
    const official = { ...position, officialVersionId: '55555555-5555-4555-8555-555555555555', duty: '开发三维视觉算法', requirement: '具备视觉算法工程经验', headcount: 1, lastObservedAt: '2026-09-09T00:00:00Z', sourceChangedAt: '2026-09-09T00:00:00Z' };
    Object.assign(r12, { officialVersions: vi.fn().mockResolvedValue([official]) });
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    const textarea = container.querySelector<HTMLTextAreaElement>('textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '正在讨论的要求');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      container.querySelector<HTMLButtonElement>('.hr-position-picker-trigger')!.click();
    });
    await act(async () => new Promise(resolve => setTimeout(resolve, 15)));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('.hr-position-picker-option')].find(item => item.textContent?.includes('视觉算法工程师'))!.click());
    const inspect = [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === '查看 JD / JR');
    expect(inspect).toBeDefined();
    await act(async () => inspect!.click());
    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).not.toBeNull();
    expect(container.textContent).toContain('开发三维视觉算法');
    expect(container.textContent).toContain('具备视觉算法工程经验');
    expect(container.textContent).toContain('对话中已确认的岗位标准');
    expect(container.textContent).toContain('尚无已确认标准');
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="关闭岗位资料"]')!.click());
    expect(textarea.value).toBe('正在讨论的要求');
    expect(location.pathname).toBe('/hr/');
    expect(startConversation).not.toHaveBeenCalled();
  });
  it("opens a conversation-first HR workspace at the canonical root", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} />));

    expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).not.toBeNull();
    expect(container.textContent).not.toContain("官网岗位");
    expect(container.querySelector(".agent-task-starter")).toBeNull();
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

  it("keeps the visited company workspace mounted and inaccessible while chat is active", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    const panel = container.querySelector<HTMLElement>(".hr-workspace-panorama-panel")!;
    const search = container.querySelector<HTMLInputElement>("[aria-label='模拟公司搜索']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "Acme");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => root.render(<HrWorkspacePage account={account} />));
    expect(container.querySelector(".hr-workspace-panorama-panel")).toBe(panel);
    expect(panel.hidden).toBe(true);
    expect(panel.getAttribute("aria-hidden")).toBe("true");
    expect(search.value).toBe("Acme");

    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    expect(container.querySelector(".hr-workspace-panorama-panel")).toBe(panel);
    expect(search.value).toBe("Acme");
  });

  it("unmounts retained company reading state when the account changes", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    const first = container.querySelector<HTMLElement>("[data-panorama-workspace]")!;
    const search = container.querySelector<HTMLInputElement>("[aria-label='模拟公司搜索']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "Acme");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => root.render(<HrWorkspacePage
      account={{ ...account, internal_user_id: "other-user" }} panorama
    />));
    expect(container.querySelector("[data-panorama-workspace]")).not.toBe(first);
    expect(container.querySelector<HTMLInputElement>("[aria-label='模拟公司搜索']")?.value).toBe("");
    expect(container.querySelectorAll("[data-panorama-workspace]")).toHaveLength(1);
  });

  it("preserves the company panel scroll position without scheduling window restoration", async () => {
    const requestFrame = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    const panel = container.querySelector<HTMLElement>(".hr-workspace-panorama-panel")!;
    panel.scrollTop = 720;
    await act(async () => panel.dispatchEvent(new Event("scroll", { bubbles: true })));
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));

    expect(container.querySelector(".hr-workspace-panorama-panel")).toBe(panel);
    expect(panel.scrollTop).toBe(720);
    expect(requestFrame).not.toHaveBeenCalled();
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("keeps the current conversation as the chat navigation target", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    await act(async () => root.render(<HrWorkspacePage account={account} positions />));

    expect(container.querySelector<HTMLAnchorElement>(
      '.hr-workspace-nav a[href="/hr/conversations/c-7"]',
    )?.textContent).toBe("对话");
  });

  it("selects explicitly without sending or replacing the draft, then returns to the known chat", async () => {
    window.history.replaceState({}, "", "/hr/conversations/c-7");
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    const textarea = container.querySelector<HTMLTextAreaElement>(".conversation-composer textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "保留我的草稿");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "带入对话")?.click());

    expect(startConversation).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/hr/conversations/c-7");
    expect(textarea.value).toBe("保留我的草稿");
    expect(container.textContent).toContain("海外岗位增长");
    expect(container.textContent).toContain("Acme Robotics");
  });

  it("scopes selected intelligence to the account and uses the free-chat draft when no chat is known", async () => {
    window.history.replaceState({}, "", "/hr/panorama");
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "带入对话")?.click());

    expect(window.location.pathname).toBe("/hr/");
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    expect(container.textContent).toContain("海外岗位增长");

    await act(async () => root.render(<HrWorkspacePage account={{ ...account, internal_user_id: "other-user" }} />));
    expect(container.textContent).not.toContain("海外岗位增长");
  });

  it("does not route a second account to the first account's retained chat when selecting intelligence", async () => {
    window.history.replaceState({}, "", "/hr/conversations/c-7");
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-7" />));
    await act(async () => root.render(<HrWorkspacePage
      account={{ ...account, internal_user_id: "other-user" }} panorama
    />));
    window.history.replaceState({}, "", "/hr/panorama");
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "带入对话")?.click());

    expect(window.location.pathname).toBe("/hr/");
  });

  it("serializes selected intelligence into one frozen new-conversation request and clears it on success", async () => {
    const send = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce({
      conversation: {
        conversation_id: "c-new", mode: "direct_agent", direct_agent_id: "hr-bot", title: "招聘对话",
        status: "active", summary_through_seq: 0, created_at: "2026-09-08T06:00:00Z",
        updated_at: "2026-09-08T06:00:00Z", archived_at: null, execution_owner: "platform",
      },
      turn: { turn_id: "turn-new" }, message: { message_id: "message-new" },
    });
    vi.mocked(startConversation).mockReturnValue({ idempotencyKey: "same", send } as never);
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "带入对话")?.click());
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "请分析");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".agent-direct-submit")?.click());
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "重新提交")?.click());

    expect(startConversation).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(2);
    const input = vi.mocked(startConversation).mock.calls[0]?.[0];
    expect(input).toEqual(expect.objectContaining({text:expect.stringContaining("请分析\n\n---\n"),scope:{positionId:null,positionCandidateIds:[],attachmentIds:[]}}));
    expect(input).toEqual(expect.objectContaining({text:expect.stringContaining('bundle_id: "bundle-7"')}));
    expect(container.textContent).not.toContain("海外岗位增长");
  });

  it("clears only the references captured by a successful pending submission", async () => {
    let finish!: (value: unknown) => void;
    const pendingResult = new Promise((resolve) => { finish = resolve; });
    vi.mocked(startConversation).mockReturnValue({
      idempotencyKey: "pending-reference", send: vi.fn().mockReturnValue(pendingResult),
    } as never);
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "带入对话")?.click());
    await act(async () => root.render(<HrWorkspacePage account={account} />));
    const textarea = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "请分析");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      container.querySelector<HTMLButtonElement>(".agent-direct-submit")?.click();
    });
    await act(async () => root.render(<HrWorkspacePage account={account} panorama />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "带入第二条")?.click());
    await act(async () => finish({
      conversation: {
        conversation_id: "c-new", mode: "direct_agent", direct_agent_id: "hr-bot", title: "招聘对话",
        status: "active", summary_through_seq: 0, created_at: "2026-09-08T06:00:00Z",
        updated_at: "2026-09-08T06:00:00Z", archived_at: null, execution_owner: "platform",
      }, turn: { turn_id: "turn-new" }, message: { message_id: "message-new" },
    }));
    await act(async () => root.render(<HrWorkspacePage account={account} />));

    expect(container.textContent).not.toContain("海外岗位增长");
    expect(container.textContent).toContain("新增情报");
  });


});
