/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Account } from "../../auth";
import { HrPositionWorkspace } from "./HrPositionWorkspace";

const positionId = "00000000-0000-4000-8000-000000000001";
const account: Account = { internal_user_id: "member", display_name: "HR", role: "member", departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf" };
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

it("completes the position and candidate workflow after remount", async () => {
  const position = vi.fn().mockResolvedValue({ positionId, sourceKind: "manual", officialJobId: null, title: "招聘负责人", department: null, locations: [], officialStatus: null, internalStatus: "active", sourceVersion: null, rowVersion: 1, createdAt: "2026-09-04T00:00:00Z", updatedAt: "2026-09-04T00:00:00Z", conversationCount: 0, materialCount: 0, artifactCount: 0, conversationIds: [], materialAttachmentIds: [], artifactIds: [], artifactAttachmentIds: [] });
  const r12Api = { startTask: vi.fn().mockResolvedValue({ taskId: "task", status: "running", taskKind: "talent_profile" }), resources: vi.fn().mockResolvedValue({ materials: [], artifacts: [] }), context: vi.fn().mockResolvedValue({ current: null, drafts: [], history: [] }), confirmContext: vi.fn(), candidateDrafts: vi.fn().mockResolvedValue([]), retryDraft: vi.fn(), confirmDraft: vi.fn(), downloadResource: vi.fn() };
  const view = <HrPositionWorkspace account={account} positionId={positionId} section="chat" api={{ position, promoteMaterial: vi.fn(), removeMaterial: vi.fn() }} r12Api={r12Api as never} loadPositionConversations={vi.fn().mockResolvedValue([])} loadCatalog={vi.fn().mockResolvedValue([])} />;
  await act(async () => root.render(view));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成人才画像")?.click());
  expect(r12Api.startTask).toHaveBeenCalledWith(positionId, "talent_profile", expect.any(String), expect.any(Object));
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<HrPositionWorkspace {...view.props} runningTask />));
  expect(container.textContent).toContain("任务仍在执行");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "候选人")?.click());
  expect([...container.querySelectorAll<HTMLButtonElement>("button")].some((button) => button.textContent === "批量上传简历")).toBe(true);
});
