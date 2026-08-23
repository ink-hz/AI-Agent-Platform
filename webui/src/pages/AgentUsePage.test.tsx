/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type { AgentCapabilityCard, Mission } from "../brainTypes";
import { AgentUseDirectoryPage } from "./AgentUseDirectoryPage";
import { AgentUsePage } from "./AgentUsePage";


const account: Account = {
  internal_user_id: "member", display_name: "磐德", role: "member",
  departments: [], gender: null,
  observation_agent_ids: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
};
const card: AgentCapabilityCard = {
  agent_id: "hr-bot", display_name: "HR Agent", domain_group: "HR",
  mission: "帮助员工和管理者完成招聘、人事与员工服务任务。",
  capabilities: ["梳理岗位需求与候选人画像"], exclusions: ["不代替管理者作出录用决定"],
  example_tasks: ["根据岗位说明梳理候选人能力组合"], required_inputs: ["任务目标"],
  accepted_input_types: ["text"], output_types: ["text"], supports_attachments_in: false,
  supports_attachments_out: false, supports_evidence: true, supports_streaming: true,
  supports_cancellation: true, supports_idempotency: true, max_duration_seconds: 300,
  data_classification: "internal", adapter_id: "metabot-core-chat", capability_version: 1,
};
const mission: Mission = {
  mission_id: "4e2ac19d-00cc-43ca-a953-f678b8bf7029", mode: "direct_agent", direct_agent_id: "hr-bot",
  status: "delegated", cancel_requested: false, row_version: 1,
  created_at: "2026-08-22T10:00:00Z", updated_at: "2026-08-22T10:00:00Z", terminal_at: null,
  prompt: "找人", content_available: true,
};


describe("professional Agent use pages", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("lists only the authorized capability catalog without management metrics", async () => {
    const loadCatalog = vi.fn().mockResolvedValue([card]);
    await act(async () => root.render(<AgentUseDirectoryPage loadCatalog={loadCatalog} />));
    expect(container.textContent).toContain("HR Agent");
    expect(container.textContent).toContain(card.mission);
    expect(container.textContent).toContain("梳理岗位需求与候选人画像");
    expect(container.textContent).not.toContain("累计 Session");
    expect(container.querySelector("a[href='/agents/hr-bot']")).not.toBeNull();
  });

  it("starts a direct Agent Mission through the platform API contract", async () => {
    const send = vi.fn().mockResolvedValue(mission);
    const createSubmission = vi.fn().mockReturnValue({ idempotencyKey: "same", send });
    const onOpenMission = vi.fn();
    await act(async () => root.render(<AgentUsePage
      account={account} agentId="hr-bot" loadCatalog={vi.fn().mockResolvedValue([card])}
      createSubmission={createSubmission} onOpenMission={onOpenMission}
    />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "找视觉人才");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>("button[type=submit]")?.click());

    expect(createSubmission).toHaveBeenCalledWith("找视觉人才", "csrf", "hr-bot");
    expect(send).toHaveBeenCalledTimes(1);
    expect(onOpenMission).toHaveBeenCalledWith(`/missions/${mission.mission_id}`);
  });

  it("recovers when navigation changes from an unavailable Agent to an authorized one", async () => {
    const loadCatalog = vi.fn().mockResolvedValue([card]);
    const props = { account, loadCatalog, createSubmission: vi.fn(), onOpenMission: vi.fn() };

    await act(async () => root.render(<AgentUsePage {...props} agentId="missing-bot" />));
    expect(container.textContent).toContain("暂时无法读取");

    await act(async () => root.render(<AgentUsePage {...props} agentId="hr-bot" />));
    expect(container.querySelector("h1")?.textContent).toBe("HR Agent");
    expect(container.querySelector("textarea")).not.toBeNull();
  });

  it("blocks direct-Agent text over the exact UTF-8 byte limit", async () => {
    const createSubmission = vi.fn();
    await act(async () => root.render(<AgentUsePage
      account={account} agentId="hr-bot" loadCatalog={vi.fn().mockResolvedValue([card])}
      createSubmission={createSubmission} onOpenMission={vi.fn()}
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
});
