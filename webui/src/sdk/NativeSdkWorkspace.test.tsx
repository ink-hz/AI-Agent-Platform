/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { NativeSdkWorkspace } from "./NativeSdkWorkspace";
import type { NativeSdkApi } from "./nativeSdkApi";


const account = {
  internal_user_id: "11111111-1111-4111-8111-111111111111", display_name: "苍渊", role: "member",
  departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [],
  directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
} as Account;

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
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

function client(): NativeSdkApi {
  return {
    requirements: vi.fn().mockResolvedValue({ items: [{
      id: 69, title: "IMU 时间戳优化", status: "reviewing", priority: 1, version: 2,
      opened_by: "sdk_import", opened_by_name: "导入账号", assigned_to: "sdk_owner",
      assigned_to_name: "行云", opened_date: "2026-10-20 10:00:00", assigned_date: "",
      business_proposer: null, source_module: "SDK 功能及文档优化", q4_source: true,
    }], page: 1, total: 1 }),
    requirement: vi.fn().mockResolvedValue({
      id: 69, title: "IMU 时间戳优化", status: "reviewing", priority: 1, version: 2,
      edit_token: "a".repeat(64),
      opened_by: "sdk_import", opened_by_name: "导入账号", assigned_to: "sdk_owner",
      assigned_to_name: "行云", opened_date: "2026-10-20 10:00:00", assigned_date: "",
      business_proposer: null, source_module: "SDK 功能及文档优化", q4_source: true,
      spec: "<p>客户场景<script>window.evil()</script></p>", verify: "<p>时间戳连续</p>",
      source_note: "2026年Q4上线版本｜SDK 功能及文档优化｜Excel第99行",
      source: { proposer_name: null, responsible_raw: "行云", planned_start: "2026-10-22",
        planned_end: "2026-10-29", source_version: "v2.11", source_risk: "", source_status: "",
        source_note: "", module: "SDK 功能及文档优化" },
      reviewer: ["cangyuan"], tasks: [], actions: [{ id: 8, action: "reviewed", actor: "cangyuan",
        actor_name: "苍渊", date: "2026-10-21 09:00:00", comment: "请补充验证" }],
    }),
    myTasks: vi.fn().mockResolvedValue({ items: [{ id: 4, name: "修复 IMU", status: "doing", assignee: "sdk_owner",
      assignee_name: "行云", story_id: 69, opened_date: "2026-10-20", planned_start: "2026-10-22",
      deadline: "2026-10-29" }], page: 1, total: 1 }),
    task: vi.fn().mockResolvedValue({ id: 4, name: "修复 IMU", status: "doing", assignee: "sdk_owner",
      assignee_name: "行云", story_id: 69, opened_date: "2026-10-20", planned_start: "2026-10-22",
      deadline: "2026-10-29", description: "<p>定位时钟漂移</p>", execution_id: 1, execution_name: "SDK Q4", actions: [] }),
    members: vi.fn().mockResolvedValue({ items: [] }),
    createRequirement: vi.fn(), pendingCreate: vi.fn().mockResolvedValue({ status: "none" }),
    editRequirement: vi.fn(), assign: vi.fn(), review: vi.fn(),
  } as NativeSdkApi;
}

async function fill(name: string, value: string) {
  const field = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[name="${name}"]`)!;
  const descriptor = Object.getOwnPropertyDescriptor(field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!;
  await act(async () => {
    descriptor.set!.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("opens a real requirement form with separate proposer, owner and reviewer", async () => {
  const api = client();
  api.members = vi.fn().mockResolvedValue({ items: [
    { account: "sdk_owner", name: "行云" }, { account: "cangyuan", name: "苍渊" },
  ] });
  await act(async () => root.render(<NativeSdkWorkspace account={account} client={api} />));
  const create = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("新建需求"));
  await act(async () => create?.click());
  expect(api.members).toHaveBeenCalledOnce();
  expect(container.querySelector('input[name="business_proposer"]')).not.toBeNull();
  expect(container.querySelector('select[name="assigned_to"]')).not.toBeNull();
  expect(container.querySelector('select[name="reviewer"]')).not.toBeNull();
  expect(container.querySelector<HTMLSelectElement>('select[name="reviewer"]')?.required).toBe(true);
  expect(container.querySelector('a[href^="https://47.106.112.69"]')).toBeNull();
});

it("offers a version-bound review for an imported Q4 story", async () => {
  const api = client();
  api.review = vi.fn().mockImplementation(async () => api.requirement(69));
  await act(async () => root.render(<NativeSdkWorkspace account={account} client={api} />));
  const story = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("IMU 时间戳优化"));
  await act(async () => story?.click());
  const review = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("评审需求"));
  await act(async () => review?.click());
  expect(container.querySelector('form[aria-label="评审需求"]')).not.toBeNull();
  expect(container.textContent).toContain("当前版本 2");
  await act(async () => container.querySelector<HTMLButtonElement>('form[aria-label="评审需求"] button[type="submit"]')?.click());
  expect(api.review).toHaveBeenCalledWith(69, { expected_version: 2, expected_token: "a".repeat(64), decision: "pass", comment: "" }, "csrf");
});

it("locks an uncertain create to the original request identity", async () => {
  const api = client();
  api.members = vi.fn().mockResolvedValue({ items: [{ account: "cangyuan", name: "苍渊" }] });
  api.createRequirement = vi.fn().mockResolvedValue({ status: "reconciliation_required", request_id: "same-request" });
  await act(async () => root.render(<NativeSdkWorkspace account={account} client={api} />));
  await act(async () => Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("新建需求"))?.click());
  await fill("title", "新设备支持");
  await fill("scenario", "客户现场");
  await fill("device_platform", "Gemini 330 / Linux");
  await fill("acceptance", "连续运行一小时");
  await act(async () => {
    const reviewer = container.querySelector<HTMLSelectElement>('select[name="reviewer"]')!;
    reviewer.value = "cangyuan";
    reviewer.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => container.querySelector<HTMLButtonElement>('form[aria-label="新建需求"] button[type="submit"]')?.click());
  expect(api.createRequirement).toHaveBeenCalledOnce();
  expect(container.querySelector<HTMLFieldSetElement>(".sdk-native-fieldset")?.disabled).toBe(true);
  expect(container.querySelector<HTMLButtonElement>(".sdk-native-toolbar .sdk-native-primary")?.disabled).toBe(true);
  await act(async () => container.querySelector<HTMLButtonElement>('form[aria-label="新建需求"] button[type="submit"]')?.click());
  expect(api.createRequirement).toHaveBeenCalledTimes(2);
  expect((api.createRequirement as ReturnType<typeof vi.fn>).mock.calls[1][0].request_id)
    .toBe((api.createRequirement as ReturnType<typeof vi.fn>).mock.calls[0][0].request_id);
});

it("warns when a created requirement still needs its selected owner assigned", async () => {
  const api = client();
  api.pendingCreate = vi.fn().mockResolvedValue({ status: "created", story_id: 70,
    requirement: await api.requirement(70), assignment_warning: true });
  await act(async () => root.render(<NativeSdkWorkspace account={account} client={api} />));
  expect(container.querySelector('[role="status"]')?.textContent)
    .toContain("负责人分配未成功。请在需求详情中重新分配");
});

it("opens Q4 ZenTao requirements and distinguishes business proposer, importer and owner", async () => {
  const api = client();
  await act(async () => root.render(<NativeSdkWorkspace account={account} client={api} />));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
  expect(api.requirements).toHaveBeenCalledWith({ view: "q4", page: 1, query: "", status: "", owner: "" });
  expect(container.textContent).toContain("IMU 时间戳优化");
  expect(container.textContent).toContain("1 条需求");
  const story = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("IMU 时间戳优化"));
  await act(async () => story?.click());
  expect(api.requirement).toHaveBeenCalledWith(69);
  expect(container.textContent).toContain("原计划未提供");
  expect(container.textContent).toContain("禅道录入人");
  expect(container.textContent).toContain("导入账号");
  expect(container.textContent).toContain("行云");
  expect(container.textContent).toContain("2026-10-22");
  expect(container.textContent).toContain("时间戳连续");
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector('a[href^="https://47.106.112.69"]')).toBeNull();
});

it("switches to employee tasks without a browser handoff", async () => {
  const api = client();
  await act(async () => root.render(<NativeSdkWorkspace account={account} client={api} />));
  const tasks = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("我的任务"));
  await act(async () => tasks?.click());
  expect(api.myTasks).toHaveBeenCalledWith(1);
  expect(container.textContent).toContain("修复 IMU");
  expect(container.textContent).toContain("2026-10-29");
  await act(async () => Array.from(container.querySelectorAll<HTMLButtonElement>("button"))
    .find(button => button.textContent?.includes("修复 IMU"))?.click());
  expect(api.task).toHaveBeenCalledWith(4);
  expect(container.textContent).toContain("定位时钟漂移");
});
