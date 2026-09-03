/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type { AgentCapabilityCard } from "../brainTypes";
import type { ConversationSubmissionResult } from "../conversationTypes";
import { DirectAgentWorkspace } from "../workspaces/direct/DirectAgentWorkspace";
import { AgentUseDirectoryPage } from "./AgentUseDirectoryPage";


const account: Account = {
  internal_user_id: "member", display_name: "磐德", role: "member",
  departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
};
const card: AgentCapabilityCard = {
  agent_id: "hr-bot", display_name: "HR Agent", domain_group: "HR",
  persona_subtitle: "Hannah · 技术人才搜寻与招聘协作",
  mission: "帮助员工和管理者完成招聘、人事与员工服务任务。",
  capabilities: ["梳理岗位需求与候选人画像"], exclusions: ["不代替管理者作出录用决定"],
  example_tasks: ["根据岗位说明梳理候选人能力组合"], required_inputs: ["任务目标"],
  accepted_input_types: ["text"], output_types: ["text"], supports_attachments_in: false,
  supports_attachments_out: false, attachment_limits: null,
  supports_evidence: true, supports_streaming: true,
  supports_cancellation: true, supports_idempotency: true, max_duration_seconds: 300,
  data_classification: "internal", adapter_id: "metabot-core-chat", capability_version: 1,
  adapter_kind: "metabot_local", adapter_config_version: 1,
  output_contract: "normalized_task_result_v1",
  interaction_modes: ["direct_chat", "brain_delegation"], workspace_url: null,
};
const marketingCards: AgentCapabilityCard[] = [
  ["marketing-prospecting-bot", "Marketing Prospecting"],
  ["marketing-inbound-bot", "Marketing Inbound"],
  ["marketing-voice-bot", "Marketing Voice"],
  ["marketing-intelligence-bot", "Marketing Intelligence"],
  ["marketing-gtm-bot", "Marketing GTM"],
].map(([agentId, displayName]) => ({
  ...card, agent_id: agentId, display_name: displayName, domain_group: "Marketing",
}));
const marketingCard = marketingCards[4];
const adminCard: AgentCapabilityCard = {
  ...card, agent_id: "ai-admin-agent", display_name: "AI 行政 Agent", domain_group: "行政服务",
  interaction_modes: ["external_workspace"], workspace_url: "/office/?view=services",
  adapter_id: null, adapter_kind: null,
};
const faeCard: AgentCapabilityCard = {
  ...adminCard, agent_id: "ai-fae-agent", display_name: "AI FAE Agent", domain_group: "技术支持",
  workspace_url: "/fae/",
};
const vocCard: AgentCapabilityCard = {
  ...adminCard, agent_id: "voc", display_name: "VOC 洞察助手", domain_group: "客户洞察",
  workspace_url: "/voc/",
};
const result: ConversationSubmissionResult = {
  conversation: {
    conversation_id: "8c13c965-1b60-472e-b275-199987d1d109", mode: "direct_agent", direct_agent_id: "hr-bot",
    title: "找人", status: "active", summary_through_seq: 0, created_at: "2026-08-22T10:00:00Z",
    updated_at: "2026-08-22T10:00:00Z", archived_at: null,
  },
  message: {
    message_id: "message", conversation_id: "8c13c965-1b60-472e-b275-199987d1d109", seq: 1, role: "user",
    content: "找人", turn_id: "turn", delivery_status: "accepted",
    created_at: "2026-08-22T10:00:00Z", completed_at: null,
    input_attachments: [], output_attachments: [], active_attachment_ids: [],
  },
  turn: {
    turn_id: "turn", conversation_id: "8c13c965-1b60-472e-b275-199987d1d109", user_message_id: "message",
    assistant_message_id: null, retry_of_turn_id: null, status: "accepted",
    created_at: "2026-08-22T10:00:00Z", updated_at: "2026-08-22T10:00:00Z",
  },
};
const historyClient = { list: vi.fn().mockResolvedValue({ items: [], next_cursor: null }) };


describe("professional Agent use pages", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    historyClient.list.mockClear();
    historyClient.list.mockResolvedValue({ items: [], next_cursor: null });
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("renders the complete nine-Agent launch matrix in product order", async () => {
    const loadCatalog = vi.fn().mockResolvedValue([
      adminCard, marketingCards[4], vocCard, marketingCards[1], card,
      marketingCards[3], faeCard, marketingCards[0], marketingCards[2],
    ]);
    await act(async () => root.render(<AgentUseDirectoryPage loadCatalog={loadCatalog} />));
    expect(container.textContent).toContain("HR Agent");
    expect(container.textContent).toContain(card.mission);
    expect(container.textContent).toContain("梳理岗位需求与候选人画像");
    expect(container.textContent).not.toContain("累计 Session");
    expect(container.querySelector("a[href='/hr/']")).not.toBeNull();
    expect(container.querySelectorAll(".agent-use-groups h2")).toHaveLength(0);
    const cards = [...container.querySelectorAll<HTMLElement>(".agent-use-card")];
    expect(cards[0].textContent).toContain("AI FAE Agent");
    expect(cards[cards.length - 1].textContent).toContain("AI 行政 Agent");
    const expectedLaunches = [
      "/fae/",
      "/hr/",
      "/voc/",
      "/marketing/prospecting",
      "/marketing/inbound",
      "/marketing/voice",
      "/marketing/intelligence",
      "/marketing/gtm",
      "/office/?view=services",
    ];
    expect(cards).toHaveLength(9);
    expect(cards.map((node) => node.getAttribute("href"))).toEqual(expectedLaunches);
    expect(cards.every((node) => !node.getAttribute("href")?.startsWith("/agents/"))).toBe(true);
    expect(container.querySelector("a[href='/office/?view=services']")?.textContent).toContain("AI 行政 Agent");
    expect(container.querySelector("a[href='/fae/']")?.textContent).toContain("AI FAE Agent");
    expect(container.querySelector("a[href='/fae/']")?.getAttribute("data-agent-kind")).toBe("fae");
    expect(container.querySelector("a[href='/hr/']")?.getAttribute("data-agent-kind")).toBe("hr");
    expect(container.querySelector("a[href='/voc/']")?.getAttribute("data-agent-kind")).toBe("voc");
    expect(container.querySelector("a[href='/marketing/gtm']")?.getAttribute("data-agent-kind")).toBe("marketing");
    expect(container.querySelector("a[href='/office/?view=services']")?.getAttribute("data-agent-kind")).toBe("admin");
    expect(container.querySelectorAll(".agent-use-card-action")).toHaveLength(9);
    expect(container.querySelectorAll(".agent-use-card-arrow")).toHaveLength(9);
    expect([...container.querySelectorAll(".agent-use-card-action")]
      .every((node) => node.textContent?.includes("打开"))).toBe(true);
    expect([...container.querySelectorAll(".agent-use-card-availability")]
      .every((node) => node.textContent === "可用")).toBe(true);
    expect(container.querySelector("a[href='/hr/']")?.getAttribute("aria-label")).toBe("打开 HR Agent 工作区");
    expect(container.querySelector("a[href='/fae/']")?.getAttribute("aria-label")).toBe("打开 AI FAE Agent 工作区");
  });

  it("never renders an unallowlisted external workspace URL", async () => {
    const poisoned = { ...vocCard, workspace_url: "https://evil.example/" };
    await act(async () => root.render(<AgentUseDirectoryPage loadCatalog={vi.fn().mockResolvedValue([poisoned])} />));

    expect(container.querySelector("a[href='https://evil.example/']")).toBeNull();
    expect(container.textContent).toContain("入口暂不可用");
  });

  it("does not invent a workspace link for an unknown catalog Agent", async () => {
    const unknown = { ...card, agent_id: "unknown-agent", display_name: "Unknown Agent" };
    await act(async () => root.render(<AgentUseDirectoryPage loadCatalog={vi.fn().mockResolvedValue([unknown])} />));

    expect(container.querySelector("a[href='/agents/unknown-agent']")).toBeNull();
    expect(container.querySelectorAll(".agent-use-card")).toHaveLength(0);
    expect(container.textContent).toContain("暂时没有可用的专业 Agent");
  });

  it("starts a direct Agent Conversation through the platform API contract", async () => {
    const send = vi.fn().mockResolvedValue(result);
    const createSubmission = vi.fn().mockReturnValue({ idempotencyKey: "same", send });
    const onOpenConversation = vi.fn();
    await act(async () => root.render(<DirectAgentWorkspace
      account={account} agentId="hr-bot" loadCatalog={vi.fn().mockResolvedValue([card])}
      createSubmission={createSubmission} historyClient={historyClient} onOpenConversation={onOpenConversation}
      conversationPath={(conversationId) => `/hr/conversations/${encodeURIComponent(conversationId)}`}
    />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "找视觉人才");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>("button[type=submit]")?.click());

    expect(createSubmission).toHaveBeenCalledWith("找视觉人才", "csrf", "hr-bot");
    expect(send).toHaveBeenCalledTimes(1);
    expect(onOpenConversation).toHaveBeenCalledWith(`/hr/conversations/${result.conversation.conversation_id}`);
    expect(historyClient.list).toHaveBeenCalledWith(expect.any(AbortSignal), undefined, 20, "hr-bot");
  });

  it("recovers when navigation changes from an unavailable Agent to an authorized one", async () => {
    const loadCatalog = vi.fn().mockResolvedValue([card]);
    const props = { account, loadCatalog, historyClient, createSubmission: vi.fn(), onOpenConversation: vi.fn() };

    await act(async () => root.render(<DirectAgentWorkspace
      {...props} agentId="missing-bot" conversationPath={(conversationId) => `/missing/${conversationId}`}
    />));
    expect(container.textContent).toContain("暂时无法读取");

    await act(async () => root.render(<DirectAgentWorkspace
      {...props} agentId="hr-bot" conversationPath={(conversationId) => `/hr/conversations/${conversationId}`}
    />));
    expect(container.querySelector("h1")?.textContent).toBe("HR Agent");
    expect(container.querySelector("textarea")).not.toBeNull();
  });

  it("blocks direct-Agent text over the exact UTF-8 byte limit", async () => {
    const createSubmission = vi.fn();
    await act(async () => root.render(<DirectAgentWorkspace
      account={account} agentId="hr-bot" loadCatalog={vi.fn().mockResolvedValue([card])}
      createSubmission={createSubmission} historyClient={historyClient} onOpenConversation={vi.fn()}
      conversationPath={(conversationId) => `/hr/conversations/${conversationId}`}
    />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "人".repeat(11_000));
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(container.textContent).toContain("32 KiB");
    expect(container.querySelector<HTMLButtonElement>("button[type=submit]")?.disabled).toBe(true);
    expect(createSubmission).not.toHaveBeenCalled();
  });

  it("keeps HR free of Marketing controls and offers non-submitting task starters", async () => {
    const createSubmission = vi.fn();
    await act(async () => root.render(<DirectAgentWorkspace
      account={account} agentId="hr-bot"
      loadCatalog={vi.fn().mockResolvedValue([card, marketingCard])}
      createSubmission={createSubmission} historyClient={historyClient} onOpenConversation={vi.fn()}
      conversationPath={(conversationId) => `/hr/conversations/${conversationId}`}
    />));

    expect(container.querySelector("nav[aria-label='Marketing Agent 切换']")).toBeNull();
    expect(container.textContent).toContain("Hannah · 技术人才搜寻与招聘协作");
    const starter = container.querySelector<HTMLButtonElement>(".agent-task-starter");
    expect(starter?.textContent).toContain("候选人能力组合");
    await act(async () => starter?.click());
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.value)
      .toBe(card.example_tasks[0]);
    expect(createSubmission).not.toHaveBeenCalled();
  });
});
