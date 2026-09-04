/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrPositionResourcesPanel } from "./HrPositionResourcesPanel";
const positionId = "00000000-0000-4000-8000-000000000001";
const artifactId = "00000000-0000-4000-8000-000000000002";
const materialId = "00000000-0000-4000-8000-000000000003";
const conversationId = "00000000-0000-4000-8000-000000000004";
const turnId = "00000000-0000-4000-8000-000000000005";
const ticketPath = `/api/v1/attachments/content/${"a".repeat(32)}`;
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { window.history.replaceState({}, "", "/_preview/dingtalk-r1/hr/"); container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

function api() {
  return { resources: vi.fn().mockResolvedValue({ materials: [{ attachmentId: materialId, filename: "岗位说明.pdf", mediaType: "application/pdf", state: "scanning", sizeBytes: 2048, createdAt: "2026-09-04T00:00:00Z", sourceConversationId: conversationId, sourceTurnId: turnId, previewAvailable: false, downloadAvailable: false }], artifacts: [{ artifactId, attachmentId: artifactId, artifactVersion: 2, filename: "面试方案.docx", mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", state: "ready", sizeBytes: 4096, createdAt: "2026-09-04T01:00:00Z", sourceConversationId: conversationId, sourceTurnId: turnId, previewAvailable: false, downloadAvailable: true }] }), downloadResource: vi.fn().mockResolvedValue({ contentPath: ticketPath, expiresAt: "2026-09-04T00:05:00Z" }) };
}

it("shows exact resource metadata with user-language availability and a prefixed safe download", async () => {
  vi.spyOn(window, "open").mockImplementation(() => null); const client = api();
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} />));
  expect(container.textContent).toContain("成果 v2"); expect(container.textContent).toContain("4 KB");
  expect(container.textContent).toContain("来源对话 00000000"); expect(container.textContent).toContain("正在安全检查，暂不可使用");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载面试方案.docx")?.click());
  expect(client.downloadResource).toHaveBeenCalledWith(positionId, artifactId, expect.any(String), "download", expect.any(AbortSignal));
  expect(window.open).toHaveBeenCalledWith(`/_preview/dingtalk-r1${ticketPath}`, "_blank", "noopener,noreferrer");
});

it("downloads only explicitly selected available resources as a safe batch", async () => {
  vi.spyOn(window, "open").mockImplementation(() => null); const client = api();
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} />));
  const checkbox = container.querySelector<HTMLInputElement>(`input[value="${artifactId}"]`)!;
  await act(async () => checkbox.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载已选 1 项")?.click());
  expect(client.downloadResource).toHaveBeenCalledTimes(1);
  expect(client.downloadResource).toHaveBeenCalledWith(positionId, artifactId, expect.any(String), "download", expect.any(AbortSignal));
});
