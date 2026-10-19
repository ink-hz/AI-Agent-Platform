/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type { SdkApi } from "./sdkApi";
import { SdkWorkbench } from "./SdkWorkbench";

const account = {
  internal_user_id: "11111111-1111-4111-8111-111111111111", display_name: "SDK 开发者", role: "member",
  departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [],
  directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
} as Account;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

function client(): SdkApi {
  return {
    me: vi.fn().mockResolvedValue({ role: "contributor", can_manage: false, read_only: false, requirements_enabled: false, plan_enabled: true }),
    requirements: vi.fn(), createRequirement: vi.fn(), createPlanRequirement: vi.fn(),
    planImports: vi.fn().mockResolvedValue({ items: [{ id: 8, filename: "2026.csv", sha256: "a".repeat(64), row_count: 69, task_count: 67, module_count: 17, created_at: "2026-10-19", uploaded_by: account.internal_user_id }], page: 1, total: 1 }),
    planRows: vi.fn().mockResolvedValue({ items: [{ id: 2, import_id: 8, record_number: 2, physical_line: 2, kind: "task", module: "SDK", cells: ["SDK", "多相机预览", "甲", "", "", "", "高", "", "开发中", "<script>evil</script>"], sha256: "a".repeat(64), filename: "2026.csv", conversion_state: null, story_id: null, actor_id: null, request_id: null }], page: 1, total: 1 }),
    planRow: vi.fn().mockImplementation(async () => ({ id: 2, import_id: 8, record_number: 2, physical_line: 2, kind: "task", module: "SDK", cells: ["SDK", "多相机预览", "甲", "", "", "", "高", "", "开发中", "<script>evil</script>"], sha256: "a".repeat(64), filename: "2026.csv", conversion_state: null, story_id: null, actor_id: null, request_id: null })),
    uploadPlan: vi.fn().mockResolvedValue({ id: 8, filename: "2026.csv", sha256: "a".repeat(64), row_count: 69, task_count: 67, module_count: 17, created_at: "2026-10-19", uploaded_by: account.internal_user_id }),
  } as unknown as SdkApi;
}

it("shows plan-only mode, source position, filters and prefilled draft without creating", async () => {
  const api = client();
  await act(async () => root.render(<SdkWorkbench account={account} client={api} />));
  expect(api.requirements).not.toHaveBeenCalled();
  expect(container.textContent).toContain("69 条来源记录");
  expect(container.textContent).toContain("67 条任务");
  const module = container.querySelector('input[aria-label="按栏目筛选"]') as HTMLInputElement;
  await act(async () => { module.value = "SDK"; module.dispatchEvent(new Event("input", { bubbles: true })); });
  const select = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("多相机预览"));
  expect(select).toBeDefined();
  await act(async () => select!.click());
  expect(container.textContent).toContain("原文件第 2 行");
  expect(container.querySelector('input[value="多相机预览"]')).not.toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect(api.createRequirement).not.toHaveBeenCalled();
  expect(api.createPlanRequirement).not.toHaveBeenCalled();
  expect((Array.from(container.querySelectorAll("button")).find(button => button.textContent === "创建需求") as HTMLButtonElement).disabled).toBe(true);
});

it("uploads an actual CSV file with the enterprise CSRF token", async () => {
  const api = client();
  await act(async () => root.render(<SdkWorkbench account={account} client={api} />));
  const file = new File(["平台,任务\nSDK,测试"], "2026.csv", { type: "text/csv" });
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  const upload = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "导入 CSV");
  await act(async () => upload!.click());
  expect(api.uploadPlan).toHaveBeenCalledWith(file, "csrf");
});

it("clears a selected source row when switching batches", async () => {
  const api = client();
  vi.mocked(api.planImports).mockResolvedValue({ items: [
    { id: 8, filename: "2026.csv", sha256: "a".repeat(64), row_count: 69, task_count: 67, module_count: 17, created_at: "2026-10-19", uploaded_by: account.internal_user_id },
    { id: 9, filename: "revised.csv", sha256: "b".repeat(64), row_count: 1, task_count: 1, module_count: 1, created_at: "2026-10-20", uploaded_by: account.internal_user_id },
  ], page: 1, total: 2 });
  await act(async () => root.render(<SdkWorkbench account={account} client={api} />));
  const selectRow = Array.from(container.querySelectorAll("button")).find(button => button.textContent?.includes("多相机预览"));
  await act(async () => selectRow!.click());
  expect(container.textContent).toContain("正在整理来源");
  const batch = container.querySelector(".sdk-plan-batch select") as HTMLSelectElement;
  await act(async () => { batch.value = "9"; batch.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(container.textContent).not.toContain("正在整理来源");
  expect(container.querySelector('input[value="多相机预览"]')).toBeNull();
});

it("can open older batches beyond the first twenty", async () => {
  const api = client();
  vi.mocked(api.planImports).mockImplementation(async (page = 1) => ({ items: [{
    id: page === 1 ? 21 : 1, filename: page === 1 ? "latest.csv" : "oldest.csv",
    sha256: "a".repeat(64), row_count: 1, task_count: 1, module_count: 1,
    created_at: "2026-10-19", uploaded_by: account.internal_user_id,
  }], page, total: 21 }));
  await act(async () => root.render(<SdkWorkbench account={account} client={api} />));
  const next = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "下一批次页");
  await act(async () => next!.click());
  expect(api.planImports).toHaveBeenCalledWith(2);
  expect(container.textContent).toContain("oldest.csv");
});
