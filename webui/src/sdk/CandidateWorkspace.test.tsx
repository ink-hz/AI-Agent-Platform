/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Account } from "../auth";
import type { SdkApi } from "./sdkApi";
import { CandidateWorkspace } from "./CandidateWorkspace";

const account = { internal_user_id: "11111111-1111-4111-8111-111111111111", csrf_token: "csrf" } as Account;
let node: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => { node = document.createElement("div"); document.body.append(node); root = createRoot(node); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); node.remove(); });

it("shows the server gate and a released baseline trace", async () => {
  const client = {
    candidates: vi.fn().mockResolvedValue({ items: [{ id: 5, story_id: 12, story_version: "1", evidence_version: 6 }], page: 1, total: 1 }),
    candidate: vi.fn().mockResolvedValue({
      id: 5, story_id: 12, story_version: "1", evidence_version: 6, creator_id: account.internal_user_id,
      task_ids: [31], manifest: { packaging_sha: "a".repeat(40), sdk_version: "1.0", script_path: "build.sh", modules: {} },
      targets: [{ os: "linux", arch: "x86_64" }], builds: [{ id: 8, os: "linux", arch: "x86_64", status: "success", artifact_name: "sdk.tgz", artifact_sha256: "b".repeat(64) }],
      tests: [], approvals: [{ id: 9, evidence_version: 6, actor_id: "other", note: "已核对" }],
      releases: [{ id: 10, channel: "stable", released_at: "2026-10-19T00:00:00Z", rollback_uri: "https://example.test/rollback", artifact_sha256s: ["b".repeat(64)] }],
    }),
    gate: vi.fn().mockResolvedValue({ ready: true, reasons: [], evidence_version: 6, approved: true, approval_id: 9 }),
  } as unknown as SdkApi;
  await act(async () => root.render(<CandidateWorkspace account={account} role="viewer" readOnly={false} client={client} />));
  expect(node.textContent).toContain("候选 #5");
  expect(node.textContent).toContain("可发布");
  expect(node.textContent).toContain("stable");
  expect(node.textContent).toContain("b".repeat(64));
});

it("keeps saved evidence readable while the requirement authority is unavailable", async () => {
  const client = {
    candidates: vi.fn().mockResolvedValue({ items: [{ id: 5, story_id: 12 }], page: 1, total: 1 }),
    candidate: vi.fn().mockResolvedValue({ id: 5, story_id: 12, story_version: "1", evidence_version: 0, creator_id: account.internal_user_id,
      task_ids: [31], manifest: { packaging_sha: "a".repeat(40), sdk_version: "1.0", script_path: "build.sh", modules: {} },
      targets: [{ os: "linux", arch: "x86_64" }], builds: [], tests: [], approvals: [], releases: [] }),
    gate: vi.fn().mockRejectedValue(new Error("offline")),
  } as unknown as SdkApi;
  await act(async () => root.render(<CandidateWorkspace account={account} role="viewer" readOnly={false} client={client} />));
  expect(node.textContent).toContain("候选 #5 · SDK 1.0");
  expect(node.textContent).toContain("SDK 证据暂时无法读取");
});
