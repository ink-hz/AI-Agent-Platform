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
  const replace = vi.fn(); const popup = { opener: window, location: { replace }, close: vi.fn() };
  vi.spyOn(window, "open").mockImplementation(() => popup as never); const client = api();
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} />));
  expect(container.textContent).toContain("成果 v2"); expect(container.textContent).toContain("4 KB");
  expect(container.textContent).toContain("来源对话 00000000"); expect(container.textContent).toContain("正在安全检查，暂不可使用");
  expect(container.textContent).toContain("可下载，暂不支持预览");
  expect(container.textContent).not.toContain("可预览和下载");
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载面试方案.docx")?.click());
  expect(client.downloadResource).toHaveBeenCalledWith(positionId, artifactId, expect.any(String), "download", expect.any(AbortSignal));
  expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
  expect(popup.opener).toBeNull();
  expect(replace).toHaveBeenCalledWith(`/_preview/dingtalk-r1${ticketPath}`);
  expect(vi.mocked(window.open).mock.invocationCallOrder[0]).toBeLessThan(client.downloadResource.mock.invocationCallOrder[0]);
});

it("downloads only explicitly selected available resources as a safe batch", async () => {
  const replace = vi.fn(); const popup = { opener: window, location: { replace }, close: vi.fn() };
  vi.spyOn(window, "open").mockImplementation(() => popup as never); const client = api();
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} />));
  const checkbox = container.querySelector<HTMLInputElement>(`input[value="${artifactId}"]`)!;
  await act(async () => checkbox.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载已选 1 项")?.click());
  expect(client.downloadResource).toHaveBeenCalledTimes(1);
  expect(client.downloadResource).toHaveBeenCalledWith(positionId, artifactId, expect.any(String), "download", expect.any(AbortSignal));
  expect(window.open).toHaveBeenCalledBefore(client.downloadResource);
  expect(replace).toHaveBeenCalledWith(`/_preview/dingtalk-r1${ticketPath}`);
});

it("closes synchronously pre-opened batch windows when a ticket fails", async () => {
  const first = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
  const second = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
  vi.spyOn(window, "open").mockReturnValueOnce(first as never).mockReturnValueOnce(second as never);
  const client = api(); const secondId = "00000000-0000-4000-8000-000000000006";
  const original = (await api().resources()).artifacts[0];
  client.resources.mockResolvedValue({ materials: [], artifacts: [
    { ...original, attachmentId: artifactId },
    { ...original, artifactId: secondId, attachmentId: secondId, filename: "画像.pdf" },
  ] });
  client.downloadResource.mockResolvedValueOnce({ contentPath: ticketPath, expiresAt: "2026-09-04T00:05:00Z" }).mockRejectedValueOnce({ status: 503 });
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} />));
  for (const checkbox of container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')) await act(async () => checkbox.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载已选 2 项")?.click());
  expect(window.open).toHaveBeenCalledTimes(2);
  expect(first.location.replace).toHaveBeenCalled();
  expect(second.close).toHaveBeenCalled();
});

it("reloads visible resources when the parent refresh generation changes", async () => {
  const client = api();
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} refreshGeneration={0} />));
  client.resources.mockResolvedValue({ materials: [], artifacts: [] });
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} refreshGeneration={1} />));
  expect(client.resources).toHaveBeenCalledTimes(2);
  expect(container.textContent).toContain("暂无岗位材料");
});

it("does not issue preview or download tickets while read only", async () => {
  const client = api(); vi.spyOn(window, "open");
  client.resources.mockResolvedValue({ materials: [], artifacts: [{ ...(await api().resources()).artifacts[0], previewAvailable: true }] });
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} readOnly />));
  expect([...container.querySelectorAll<HTMLButtonElement>(".hr-resource-actions button")].every((button) => button.disabled)).toBe(true);
  expect([...container.querySelectorAll<HTMLInputElement>('.hr-resource-actions input[type="checkbox"]')].every((input) => input.disabled)).toBe(true);
  expect(client.downloadResource).not.toHaveBeenCalled();
  expect(window.open).not.toHaveBeenCalled();
});

it("reuses the ticket request id after an uncertain response", async () => {
  const popup = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
  vi.spyOn(window, "open").mockImplementation(() => ({ ...popup, location: { replace: vi.fn() }, close: vi.fn() }) as never);
  const client = api(); client.downloadResource.mockRejectedValueOnce({ status: 503 }).mockResolvedValueOnce({ contentPath: ticketPath, expiresAt: "2026-09-04T00:05:00Z" });
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} />));
  const download = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载面试方案.docx")!;
  await act(async () => download.click()); await act(async () => download.click());
  expect(client.downloadResource.mock.calls[0]?.[2]).toBe(client.downloadResource.mock.calls[1]?.[2]);
});

it("uses normal empty copy and preserves the last valid resources when refresh fails", async () => {
  const client = api();
  client.resources.mockResolvedValueOnce({ materials: [], artifacts: [] });
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} refreshGeneration={0} />));
  expect(container.textContent).toContain("暂无岗位材料");
  expect(container.textContent).toContain("暂无生成成果");

  client.resources.mockRejectedValueOnce(new Error("offline"));
  await act(async () => root.render(<HrPositionResourcesPanel api={client as never} positionId={positionId} refreshGeneration={1} />));

  expect(container.textContent).toContain("暂无岗位材料");
  expect(container.textContent).toContain("材料与成果暂时无法读取");
  expect([...container.querySelectorAll<HTMLButtonElement>("button")].some((button) => button.textContent === "重试")).toBe(true);
});
