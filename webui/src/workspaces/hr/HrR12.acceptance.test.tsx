/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Account } from "../../auth";
import { HrPositionWorkspace } from "./HrPositionWorkspace";

const positionId = "00000000-0000-4000-8000-000000000001";
const materialId = "00000000-0000-4000-8000-000000000002";
const contextId = "00000000-0000-4000-8000-000000000003";
const account: Account = { internal_user_id: "member", display_name: "HR", role: "member", departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf" };
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

it("selects exact materials, launches a quick task, restores it after remount, and opens the candidate workflow", async () => {
  const position = vi.fn().mockResolvedValue({ positionId, sourceKind: "manual", officialJobId: null, title: "招聘负责人", department: null, locations: [], officialStatus: null, internalStatus: "active", sourceVersion: null, rowVersion: 1, createdAt: "2026-09-04T00:00:00Z", updatedAt: "2026-09-04T00:00:00Z", conversationCount: 0, materialCount: 1, artifactCount: 0, conversationIds: [], materialAttachmentIds: [materialId], artifactIds: [], artifactAttachmentIds: [] });
  const context = { contextVersionId: contextId, positionId, displayVersion: 1, status: "confirmed", summary: "已确认画像", modules: { profile: { summary: "招聘负责人" } }, officialVersionId: null, baseContextVersionId: null, sourceConversationId: null, sourceTurnId: null, sourceArtifactVersionId: null, sourceMaterialAttachmentIds: [], agentId: null, modelVersion: null, rowVersion: 1, createdAt: "2026-09-04T00:00:00Z", confirmedAt: "2026-09-04T00:00:00Z" };
  const r12Api = {
    startTask: vi.fn().mockResolvedValue({ taskId: "task", status: "running", taskKind: "talent_profile" }), activeTasks: vi.fn().mockResolvedValueOnce([]).mockResolvedValue([{ taskId: "task", status: "running", taskKind: "talent_profile" }]),
    resources: vi.fn().mockResolvedValue({ materials: [{ attachmentId: materialId, filename: "岗位说明.pdf", mediaType: "application/pdf", state: "ready", sizeBytes: 2, createdAt: "2026-09-04T00:00:00Z", sourceConversationId: null, sourceTurnId: null, previewAvailable: true, downloadAvailable: true }], artifacts: [] }), context: vi.fn().mockResolvedValue({ current: context, drafts: [], history: [context] }), compareContext: vi.fn(), confirmContext: vi.fn(),
    candidateDrafts: vi.fn().mockResolvedValue([]), positionCandidates: vi.fn().mockResolvedValue([]), candidate: vi.fn(), candidateDocuments: vi.fn(), candidateAnalyses: vi.fn(), candidateFeedback: vi.fn(), retryDraft: vi.fn(), confirmDraft: vi.fn(), createCandidateDraftBatch: vi.fn(), appendCandidateFeedback: vi.fn(), compareCandidates: vi.fn(), downloadResource: vi.fn(),
  };
  const props = { account, positionId, section: "chat" as const, api: { position, promoteMaterial: vi.fn(), removeMaterial: vi.fn() }, r12Api: r12Api as never, loadPositionConversations: vi.fn().mockResolvedValue([]), loadCatalog: vi.fn().mockResolvedValue([]) };
  await act(async () => root.render(<HrPositionWorkspace {...props} />));
  await act(async () => container.querySelector<HTMLInputElement>('input[name="quick-task-material"]')?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "生成人才画像")?.click());
  expect(r12Api.startTask).toHaveBeenCalledWith(positionId, "talent_profile", expect.any(String), { contextVersionId: contextId, materialIds: [materialId] }, expect.any(AbortSignal));
  await act(async () => root.unmount()); root = createRoot(container);
  await act(async () => root.render(<HrPositionWorkspace {...props} />));
  expect(container.textContent).toContain("任务仍在执行，刷新后已恢复状态");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "候选人")?.click());
  expect(container.querySelector('section[aria-label="批量简历导入"] input[type="file"]')).not.toBeNull();
});
