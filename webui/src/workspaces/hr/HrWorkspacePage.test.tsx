/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { fetchAgentCatalog } from "../../brainApi";
import type { AgentCapabilityCard } from "../../brainTypes";
import { listConversations } from "../../conversationApi";
import { HrWorkspacePage } from "./HrWorkspacePage";


vi.mock("../../brainApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../brainApi")>(),
  fetchAgentCatalog: vi.fn(),
}));

vi.mock("../../conversationApi", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../conversationApi")>(),
  listConversations: vi.fn(),
}));


const account: Account = {
  internal_user_id: "member",
  display_name: "磐德",
  role: "member",
  departments: [],
  gender: null,
  observation_agent_ids: [],
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

  it("scopes history to the canonical HR workspace Agent", async () => {
    await act(async () => root.render(<HrWorkspacePage account={account} />));

    expect(listConversations).toHaveBeenCalledWith(expect.any(AbortSignal), undefined, 20, "hr-bot");
    expect(container.querySelector("h1")?.textContent).toBe("HR Agent");
    expect(container.querySelector('nav[aria-label="Marketing Agent 切换"]')).toBeNull();
  });

  it("opens a new HR conversation at the canonical workspace root with a trailing slash", async () => {
    window.history.replaceState({}, "", "/hr/conversations/c-1");
    await act(async () => root.render(<HrWorkspacePage account={account} conversationId="c-1" />));

    await act(async () => container.querySelector<HTMLButtonElement>(".conversation-sidebar-new")?.click());

    expect(window.location.pathname).toBe("/hr/");
  });
});
