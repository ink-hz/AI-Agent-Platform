/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrCandidateWorkspace } from "./HrCandidateWorkspace";
const positionId = "00000000-0000-4000-8000-000000000001";
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });
it("keeps two successful resume drafts when a sibling fails and retries only that item", async () => {
  const api = { candidateDrafts: vi.fn().mockResolvedValue([{ draftId: "00000000-0000-4000-8000-000000000002", filename: "a.pdf", state: "pending", candidateName: "A", error: null, attachmentId: "00000000-0000-4000-8000-000000000003" }, { draftId: "00000000-0000-4000-8000-000000000004", filename: "b.pdf", state: "pending", candidateName: "B", error: null, attachmentId: "00000000-0000-4000-8000-000000000005" }, { draftId: "00000000-0000-4000-8000-000000000006", filename: "bad.pdf", state: "failed", candidateName: null, error: "损坏", attachmentId: "00000000-0000-4000-8000-000000000007" }]), retryDraft: vi.fn().mockResolvedValue({}), confirmDraft: vi.fn(), startTask: vi.fn() };
  await act(async () => root.render(<HrCandidateWorkspace api={api as never} positionId={positionId} />));
  expect(container.textContent?.match(/待确认/g)).toHaveLength(2);
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重试解析")?.click());
  expect(api.retryDraft).toHaveBeenCalledTimes(1);
});
