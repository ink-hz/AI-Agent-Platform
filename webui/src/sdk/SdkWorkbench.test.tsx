/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type { SdkApi } from "./sdkApi";
import { SdkWorkbench } from "./SdkWorkbench";

const account: Account = {
  internal_user_id: "11111111-1111-4111-8111-111111111111",
  display_name: "SDK 开发者", role: "member", departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

it("shows live requirements and the selected story without a second login", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: "contributor", can_manage: false, read_only: false }),
    requirements: vi.fn().mockResolvedValue({ items: [{ id: 12, title: "USB 稳定性", status: "active" }], page: 1, total: 1 }),
    requirement: vi.fn().mockResolvedValue({ story: { id: "12", product: "7", title: "USB 稳定性", verify: "连续运行", status: "active" }, reviews: [], tasks: [{ story_id: 12, task_id: 31 }] }),
    candidates: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
  } as unknown as SdkApi;

  await act(async () => root.render(<SdkWorkbench account={account} storyId={12} client={client} />));

  expect(container.textContent).toContain("USB 稳定性");
  expect(container.textContent).toContain("连续运行");
  expect(container.textContent).toContain("开发任务 #31");
  expect(container.textContent).toContain("创建需求");
  expect(client.requirement).toHaveBeenCalledWith(12);
});

it("shows a clear unavailable state when the service cannot load", async () => {
  const client = {
    me: vi.fn().mockRejectedValue(new Error("unavailable")),
    requirements: vi.fn(),
  } as unknown as SdkApi;

  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));

  expect(container.textContent).toContain("暂时无法连接 SDK 工作台");
});

it("does not fetch requirements for an account without SDK membership", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: null, can_manage: false, read_only: false }),
    requirements: vi.fn(),
  } as unknown as SdkApi;

  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));

  expect(container.textContent).toContain("没有 SDK 工作台权限");
  expect(client.requirements).not.toHaveBeenCalled();
});

it("shows owner reconciliation work with stable intent identifiers", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: null, can_manage: true, read_only: false }),
    requirements: vi.fn(),
    members: vi.fn().mockResolvedValue({ items: [] }),
    reconciliation: vi.fn().mockResolvedValue({ items: [{ actor_id: account.internal_user_id, request_id: "22222222-2222-4222-8222-222222222222", kind: "create_requirement", story_id: null }] }),
  } as unknown as SdkApi;

  await act(async () => root.render(<SdkWorkbench account={{ ...account, role: "platform_owner" }} client={client} />));

  expect(container.textContent).toContain("待核对写入");
  expect(container.textContent).toContain("22222222-2222-4222-8222-222222222222");
  expect(client.requirements).not.toHaveBeenCalled();
});
