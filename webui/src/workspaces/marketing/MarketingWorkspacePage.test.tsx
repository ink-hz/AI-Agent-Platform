/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { fetchAgentCatalog } from "../../brainApi";
import {
  listConversations,
  startConversation,
  type ConversationSubmission,
} from "../../conversationApi";
import type { AgentCapabilityCard } from "../../brainTypes";
import type { ConversationSubmissionResult } from "../../conversationTypes";
import { MarketingWorkspacePage } from "./MarketingWorkspacePage";


vi.mock("../../brainApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../brainApi")>(),
  fetchAgentCatalog: vi.fn(),
}));

vi.mock("../../conversationApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../conversationApi")>(),
  listConversations: vi.fn(),
  startConversation: vi.fn(),
}));

vi.mock("../../pages/ConversationPage", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../pages/ConversationPage")>(),
  ConversationPage: ({ conversationId, expectedAgentId }: { conversationId: string; expectedAgentId: string }) => (
    <p data-testid="selected-conversation">{expectedAgentId}:{conversationId}</p>
  ),
}));


const account: Account = {
  internal_user_id: "member", display_name: "磐德", role: "member",
  departments: [], gender: null, observation_agent_ids: [], directory_freshness: "fresh",
  workspace_scopes: [],
  hard_stale_read_only: false, csrf_token: "csrf",
};

const marketingCards: AgentCapabilityCard[] = [
  ["marketing-prospecting-bot", "Marketing Prospecting"],
  ["marketing-inbound-bot", "Marketing Inbound"],
  ["marketing-voice-bot", "Marketing Voice"],
  ["marketing-intelligence-bot", "Marketing Intelligence"],
  ["marketing-gtm-bot", "Marketing GTM"],
].map(([agent_id, display_name]) => ({
  agent_id, display_name, domain_group: "Marketing", persona_subtitle: `${display_name} · 营销协作`,
  mission: "帮助团队完成营销任务。", capabilities: ["营销协作"], exclusions: ["不替代审批"],
  example_tasks: ["准备营销材料"], required_inputs: ["任务目标"], accepted_input_types: ["text"],
  output_types: ["text"], supports_attachments_in: false, supports_attachments_out: false,
  supports_evidence: true, supports_streaming: true, supports_cancellation: true,
  supports_idempotency: true, max_duration_seconds: 300, data_classification: "internal",
  adapter_id: "metabot-core-chat", capability_version: 1, adapter_kind: "metabot_local",
  adapter_config_version: 1, output_contract: "normalized_task_result_v1",
  interaction_modes: ["direct_chat", "brain_delegation"], workspace_url: null,
}));

const inboundResult: ConversationSubmissionResult = {
  conversation: {
    conversation_id: "c-1", mode: "direct_agent", direct_agent_id: "marketing-inbound-bot",
    title: "Inbound", status: "active", summary_through_seq: 0,
    created_at: "2026-09-03T10:00:00Z", updated_at: "2026-09-03T10:00:00Z", archived_at: null,
  },
  message: {
    message_id: "m-1", conversation_id: "c-1", seq: 1, role: "user", content: "draft",
    turn_id: "t-1", delivery_status: "accepted", created_at: "2026-09-03T10:00:00Z", completed_at: null,
  },
  turn: {
    turn_id: "t-1", conversation_id: "c-1", user_message_id: "m-1", assistant_message_id: null,
    retry_of_turn_id: null, status: "accepted", created_at: "2026-09-03T10:00:00Z",
    updated_at: "2026-09-03T10:00:00Z",
  },
};


describe("MarketingWorkspacePage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    window.history.replaceState({}, "", "/marketing/inbound");
    vi.mocked(fetchAgentCatalog).mockResolvedValue(marketingCards);
    vi.mocked(listConversations).mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(startConversation).mockReset();
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

  it("scopes history and new conversations to the selected canonical Marketing workspace", async () => {
    vi.mocked(startConversation).mockReturnValue({
      idempotencyKey: "same",
      send: vi.fn().mockResolvedValue(inboundResult),
    });

    await act(async () => root.render(<MarketingWorkspacePage account={account} agentSlug="inbound" />));

    expect(listConversations).toHaveBeenCalledWith(
      expect.any(AbortSignal), undefined, 20, "marketing-inbound-bot",
    );
    expect(container.querySelector<HTMLAnchorElement>('a[href="/marketing/voice"]')?.textContent).toBe("Voice");
    expect(container.querySelectorAll('nav[aria-label="Marketing Agent 切换"] a')).toHaveLength(5);

    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "draft");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>('button[type="submit"]')?.click());

    const historyPath = window.location.pathname;
    expect(historyPath).toBe("/marketing/inbound/conversations/c-1");
  });

  it("remounts the stateful workspace when switching Agents", async () => {
    let activeSignal: AbortSignal | undefined;
    vi.mocked(startConversation).mockReturnValue({
      idempotencyKey: "pending",
      send: vi.fn((signal?: AbortSignal) => {
        activeSignal = signal;
        return new Promise<ConversationSubmissionResult>(() => undefined);
      }),
    } satisfies ConversationSubmission);

    await act(async () => root.render(
      <MarketingWorkspacePage account={account} agentSlug="inbound" conversationId="c-1" />,
    ));
    expect(container.querySelector('[data-testid="selected-conversation"]')?.textContent)
      .toBe("marketing-inbound-bot:c-1");

    await act(async () => root.render(<MarketingWorkspacePage account={account} agentSlug="inbound" />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "inbound draft");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>('button[type="submit"]')?.click());
    expect(activeSignal?.aborted).toBe(false);

    await act(async () => root.render(<MarketingWorkspacePage account={account} agentSlug="voice" />));

    expect(activeSignal?.aborted).toBe(true);
    expect(container.querySelector('[data-testid="selected-conversation"]')).toBeNull();
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe("");
    expect(container.querySelector('a[aria-current="page"]')?.textContent).toBe("Voice");
    expect(listConversations).toHaveBeenCalledWith(
      expect.any(AbortSignal), undefined, 20, "marketing-voice-bot",
    );
  });
});
