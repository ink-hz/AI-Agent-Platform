/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import type { AgentCapabilityCard } from "../../brainTypes";
import { SessionMaterialsDrawer } from "../../components/conversation/SessionMaterialsDrawer";
import type { Conversation, ConversationAttachment, ConversationSubmissionResult } from "../../conversationTypes";
import type { HrPositionDetail } from "../../hrTypes";
import { HrPositionWorkspace } from "./HrPositionWorkspace";


const POSITION_ID = "11111111-1111-4111-8111-111111111111";
const CONVERSATION_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_ID = "33333333-3333-4333-8333-333333333333";
const account: Account = {
  internal_user_id: "member", display_name: "HR", role: "member", departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};
const detail: HrPositionDetail = {
  positionId: POSITION_ID, sourceKind: "official_site", officialJobId: "J11014", title: "算法工程师",
  department: "机器人", locations: ["深圳"], officialStatus: "active", internalStatus: "active",
  sourceVersion: "sync-v2", rowVersion: 1, createdAt: "2026-09-01T00:00:00Z",
  updatedAt: "2026-09-04T00:00:00Z", conversationCount: 1, materialCount: 0, artifactCount: 1,
  conversationIds: [CONVERSATION_ID], materialAttachmentIds: [], artifactIds: ["44444444-4444-4444-8444-444444444444"],
};
const card: AgentCapabilityCard = {
  agent_id: "hr-bot", display_name: "HR Agent", domain_group: "HR", persona_subtitle: "Hannah",
  mission: "招聘智能协作", capabilities: ["岗位分析"], exclusions: ["不代替录用决策"],
  example_tasks: ["梳理岗位画像"], required_inputs: ["目标"], accepted_input_types: ["text"],
  output_types: ["text"], supports_attachments_in: false, supports_attachments_out: false,
  attachment_limits: null, supports_evidence: true, supports_streaming: true, supports_cancellation: true,
  supports_idempotency: true, max_duration_seconds: 300, data_classification: "internal",
  interaction_modes: ["direct_chat"], workspace_url: null, adapter_id: "metabot-core-chat",
  adapter_kind: "metabot_local", adapter_config_version: 1, output_contract: "normalized_task_result_v1",
  capability_version: 1,
};

function conversation(id: string, title: string): Conversation {
  return { conversation_id: id, mode: "direct_agent", direct_agent_id: "hr-bot", title, status: "active",
    summary_through_seq: 0, created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:00Z",
    archived_at: null };
}

function result(): ConversationSubmissionResult {
  return {
    conversation: conversation(CONVERSATION_ID, "岗位画像"),
    message: { message_id: "message", conversation_id: CONVERSATION_ID, seq: 1, role: "user",
      content: "梳理画像", turn_id: "turn", delivery_status: "accepted", created_at: "2026-09-04T00:00:00Z",
      completed_at: null, input_attachments: [], output_attachments: [], active_attachment_ids: [] },
    turn: { turn_id: "turn", conversation_id: CONVERSATION_ID, user_message_id: "message",
      assistant_message_id: null, retry_of_turn_id: null, status: "accepted",
      created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:00Z" },
  };
}

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  }});
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks();
  delete (globalThis as { localStorage?: unknown }).localStorage;
});


it("recovers after refresh and creates work only inside the loaded position", async () => {
  const position = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(detail);
  const createSubmission = vi.fn().mockReturnValue({ idempotencyKey: "same", send: vi.fn().mockResolvedValue(result()) });
  const loadPositionConversations = vi.fn().mockResolvedValue([
    conversation(CONVERSATION_ID, "岗位画像"), conversation(OTHER_ID, "别的岗位"),
  ]);
  await act(async () => root.render(<HrPositionWorkspace
    account={account} positionId={POSITION_ID}
    api={{ position, promoteMaterial: vi.fn(), removeMaterial: vi.fn() }}
    createSubmission={createSubmission} loadCatalog={vi.fn().mockResolvedValue([card])}
    loadPositionConversations={loadPositionConversations} onOpenConversation={vi.fn()}
  />));
  expect(container.textContent).toContain("岗位工作区暂时不可用");

  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "重新加载")?.click());
  expect(container.textContent).toContain("算法工程师");
  expect(container.textContent).toContain("岗位画像");
  expect(container.textContent).not.toContain("别的岗位");
  expect(container.textContent).not.toMatch(/北森|BOSS|猎聘|Offer|候选人漏斗/);

  const composer = container.querySelector<HTMLTextAreaElement>("#direct-agent-request")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(composer, "梳理画像");
    composer.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => container.querySelector<HTMLButtonElement>("button[type=submit]")?.click());
  expect(createSubmission).toHaveBeenCalledWith("梳理画像", "csrf", "hr-bot", { positionId: POSITION_ID });
});


it("promotes a user file explicitly while keeping generated results downloadable", async () => {
  const userFile: ConversationAttachment = {
    attachmentId: "user-file", conversationId: CONVERSATION_ID, source: "user", displayName: "岗位说明.pdf",
    detectedMime: "application/pdf", sizeBytes: 1024, sha256: null, state: "ready", stateReason: null,
    createdAt: "2026-09-04T00:00:00Z", retainedUntil: "2027-09-04T00:00:00Z", preview: null, coverage: null,
  };
  const resultFile: ConversationAttachment = { ...userFile, attachmentId: "result-file", source: "agent", displayName: "面试题.docx" };
  const promote = vi.fn();
  const open = vi.fn();
  await act(async () => root.render(<SessionMaterialsDrawer
    attachments={[userFile, resultFile]} onOpen={open} onPositionMaterialChange={promote}
  />));

  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "设为岗位材料")?.click());
  const resultCard = [...container.querySelectorAll<HTMLElement>(".conversation-attachment-card")]
    .find((item) => item.textContent?.includes("面试题.docx"));
  await act(async () => [...(resultCard?.querySelectorAll<HTMLButtonElement>("button") ?? [])]
    .find((button) => button.textContent === "下载")?.click());

  expect(promote).toHaveBeenCalledWith(userFile, true);
  expect(open).toHaveBeenCalledWith(resultFile, "download");
});
