/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrPositionResourcesPanel } from "./HrPositionResourcesPanel";
const positionId = "00000000-0000-4000-8000-000000000001"; const artifactId = "00000000-0000-4000-8000-000000000002";
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });
it("previews and downloads an exact position artifact", async () => {
  vi.spyOn(window, "open").mockImplementation(() => null);
  const api = { resources: vi.fn().mockResolvedValue({ materials: [], artifacts: [{ artifactId, attachmentId: artifactId, artifactVersion: 1, filename: "面试方案.docx", mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", state: "ready", sizeBytes: 2, createdAt: "2026-09-04T00:00:00Z", sourceConversationId: null, sourceTurnId: null, previewAvailable: false, downloadAvailable: true }] }), downloadResource: vi.fn().mockResolvedValue({ contentPath: "/opaque", expiresAt: "2026-09-04T00:05:00Z" }) };
  await act(async () => root.render(<HrPositionResourcesPanel api={api as never} positionId={positionId} />));
  await act(async () => container.querySelector<HTMLButtonElement>("button")?.click());
  expect(api.downloadResource).toHaveBeenCalledWith(positionId, artifactId, expect.any(String), "download");
});
