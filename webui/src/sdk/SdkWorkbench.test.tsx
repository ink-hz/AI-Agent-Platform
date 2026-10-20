/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { SdkApiError, type SdkApi } from "./sdkApi";
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
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });

  expect(container.textContent).toContain("USB 稳定性");
  expect(container.textContent).toContain("连续运行");
  expect(container.textContent).toContain("开发任务 #31");
  expect(container.textContent).toContain("创建需求");
  expect(client.requirement).toHaveBeenCalledWith(12);
});

it("opens native ZenTao requirements and keeps the annual CSV secondary", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: "contributor", can_manage: false, read_only: false, plan_enabled: true, native_only: true }),
    requirements: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
    planImports: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
    candidates: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  expect(container.querySelector('a[href="https://47.106.112.69/"]')?.textContent).toContain("禅道");
  expect(container.querySelector("details.sdk-plan-drawer")).toBeNull();
  expect(container.querySelector(".sdk-native-entry")).not.toBeNull();
  expect(container.textContent).not.toContain("创建需求");
  expect(container.textContent).not.toContain("候选");
  expect(client.requirements).not.toHaveBeenCalled();
  expect(client.candidates).not.toHaveBeenCalled();
});

it("posts a one-use SSO ticket to ZenTao after DingTalk login", async () => {
  const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => {});
  const client = {
    me: vi.fn().mockResolvedValue({ role: "contributor", can_manage: false, read_only: false, native_only: true, sso_enabled: true }),
    ssoStart: vi.fn().mockResolvedValue({ ticket: "single-use-ticket", consume_url: "https://47.106.112.69/sdk-sso/consume" }),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  const enter = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("进入 SDK 禅道工作区"));
  expect(enter).toBeDefined();
  await act(async () => enter!.click());
  expect(client.ssoStart).toHaveBeenCalledWith("csrf", "home");
  expect(submit).toHaveBeenCalledOnce();
  const form = document.body.querySelector("form[action='https://47.106.112.69/sdk-sso/consume']") as HTMLFormElement;
  expect(form.method.toLowerCase()).toBe("post");
  expect((form.querySelector("input[name='ticket']") as HTMLInputElement).value).toBe("single-use-ticket");
});

it.each([["需求", "requirements"], ["我的任务", "tasks"], ["测试", "tests"], ["发布", "releases"]])("opens %s through a target-bound personal SSO ticket", async (label, target) => {
  vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => {});
  const client = {
    me: vi.fn().mockResolvedValue({ role: "contributor", can_manage: false, read_only: false, native_only: true, sso_enabled: true }),
    ssoStart: vi.fn().mockResolvedValue({ ticket: "single-use-ticket", consume_url: "https://47.106.112.69/sdk-sso/consume" }),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>(".sdk-native-actions button")).find(item => item.textContent?.includes(label));
  expect(button, label).toBeDefined();
  await act(async () => button!.click());
  expect(client.ssoStart).toHaveBeenLastCalledWith("csrf", target);
});

it("shows a retry hint when browser policy blocks the ZenTao handoff", async () => {
  vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => {});
  const client = {
    me: vi.fn().mockResolvedValue({ role: "contributor", can_manage: false, read_only: false, native_only: true, sso_enabled: true }),
    ssoStart: vi.fn().mockResolvedValue({ ticket: "single-use-ticket", consume_url: "https://47.106.112.69/sdk-sso/consume" }),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  const enter = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("进入 SDK 禅道工作区"))!;
  await act(async () => enter.click());
  expect(enter.disabled).toBe(true);

  const violation = new Event("securitypolicyviolation") as SecurityPolicyViolationEvent;
  Object.defineProperties(violation, {
    effectiveDirective: { value: "form-action" },
    blockedURI: { value: "https://47.106.112.69/sdk-sso/consume" },
  });
  await act(async () => document.dispatchEvent(violation));

  expect(enter.disabled).toBe(false);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("刷新 SDK 页面");
});

it("explains a first-login provisioning delay and allows another click", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: null, can_manage: false, can_enter: true, read_only: false, native_only: true, sso_enabled: true }),
    ssoStart: vi.fn().mockRejectedValue(new SdkApiError(409, "sdk_provision_in_progress")),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  const enter = container.querySelector<HTMLButtonElement>(".sdk-native-actions button")!;
  await act(async () => enter.click());
  expect(enter.disabled).toBe(false);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("开通中");
});

it("keeps the native entry available without the legacy requirements API", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: "contributor", can_manage: false, read_only: false, native_only: true }),
    requirements: vi.fn().mockRejectedValue(new Error("unavailable")),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} storyId={12} client={client} />));
  expect(container.querySelector('a[href="https://47.106.112.69/"]')).not.toBeNull();
  expect(client.requirements).not.toHaveBeenCalled();
  expect(client.requirement).toBeUndefined();
});

it("does not call plan endpoints when the plan feature is disabled", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: "viewer", can_manage: false, read_only: false, requirements_enabled: true, plan_enabled: false }),
    requirements: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
    candidates: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
    planImports: vi.fn(),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  expect(client.planImports).not.toHaveBeenCalled();
});

it("can browse beyond the first page of requirements", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: "viewer", can_manage: false, read_only: false }),
    requirements: vi.fn().mockImplementation(async (page = 1) => ({ items: [{ id: page, title: `第 ${page} 页`, status: "active" }], page, total: 100 })),
    candidates: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  const next = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "下一页");
  expect(next).toBeDefined();
  await act(async () => next!.click());
  expect(container.textContent).toContain("第 2 页");
});

it("can retrieve older requirement review history", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: "viewer", can_manage: false, read_only: false }),
    requirements: vi.fn().mockResolvedValue({ items: [{ id: 12, title: "需求", status: "active" }], page: 1, total: 1 }),
    requirement: vi.fn().mockResolvedValue({ story: { id: "12", product: "7", title: "需求", version: "1" },
      reviews: Array.from({ length: 50 }, () => ({ decision: "approved", reason: "新评审" })), tasks: [] }),
    reviews: vi.fn().mockResolvedValue({ items: [{ decision: "rejected", reason: "旧评审" }], page: 2 }),
    candidates: vi.fn().mockResolvedValue({ items: [], page: 1, total: 0 }),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={account} storyId={12} client={client} />));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
  const older = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "查看更早评审");
  expect(older).toBeDefined();
  await act(async () => older!.click());
  expect(container.textContent).toContain("旧评审");
  expect(client.reviews).toHaveBeenCalledWith(12, 2);
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

it("offers SSO entry to an eligible SDK department member before first account creation", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: null, can_manage: false, can_enter: true, read_only: false, native_only: true, sso_enabled: true }),
    ssoStart: vi.fn().mockResolvedValue({ ticket: "first-ticket", consume_url: "https://47.106.112.69/sdk-sso/consume" }),
  } as unknown as SdkApi;
  vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => {});
  await act(async () => root.render(<SdkWorkbench account={account} client={client} />));
  const first = container.querySelector<HTMLButtonElement>(".sdk-native-actions button");
  expect(first?.disabled).toBe(false);
  await act(async () => first!.click());
  expect(client.ssoStart).toHaveBeenCalledWith("csrf", "requirements");
});

it("lets the platform owner select SDK and collaborator departments from the verified directory", async () => {
  const client = {
    me: vi.fn().mockResolvedValue({ role: null, can_manage: true, read_only: false, native_only: true, sso_enabled: true }),
    ssoDepartments: vi.fn().mockResolvedValue({ items: [
      { id: "11111111-1111-4111-8111-111111111111", name: "应用SDK组", path: "研发中心 / 应用SDK组", people: 18, root: false, selected: true },
      { id: "22222222-2222-4222-8222-222222222222", name: "测试协作组", path: "质量中心 / 测试协作组", people: 12, root: false, selected: false },
      { id: "33333333-3333-4333-8333-333333333333", name: "公司", path: "公司", people: 3000, root: true, selected: false },
    ] }),
    setSsoDepartment: vi.fn().mockResolvedValue({ id: "22222222-2222-4222-8222-222222222222", enabled: true }),
  } as unknown as SdkApi;
  await act(async () => root.render(<SdkWorkbench account={{ ...account, role: "platform_owner" }} client={client} />));
  expect(container.textContent).toContain("自动开通范围");
  expect(container.textContent).toContain("应用SDK组");
  expect(container.textContent).toContain("研发中心 / 应用SDK组");
  expect(container.textContent).toContain("18 人");
  const company = Array.from(container.querySelectorAll<HTMLInputElement>("input[type=checkbox]"))
    .find(input => input.closest("label")?.textContent?.includes("公司"));
  expect(company?.disabled).toBe(true);
  const collaborator = Array.from(container.querySelectorAll<HTMLInputElement>("input[type=checkbox]"))
    .find(input => input.closest("label")?.textContent?.includes("测试协作组"));
  expect(collaborator?.checked).toBe(false);
  await act(async () => {
    collaborator!.click();
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  expect(client.setSsoDepartment).toHaveBeenCalledWith("22222222-2222-4222-8222-222222222222", true, "csrf");
  expect(collaborator?.checked).toBe(true);
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
