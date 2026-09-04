/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrPositionContextPanel } from "./HrPositionContextPanel";

const positionId = "00000000-0000-4000-8000-000000000001";
const baseId = "00000000-0000-4000-8000-000000000002";
const draftId = "00000000-0000-4000-8000-000000000003";
const currentId = "00000000-0000-4000-8000-000000000004";
const common = { positionId, officialVersionId: null, sourceConversationId: null, sourceTurnId: null, sourceArtifactVersionId: null, sourceMaterialAttachmentIds: [], agentId: null, modelVersion: null, createdAt: "2026-09-04T00:00:00Z", confirmedAt: null };
const base = { ...common, contextVersionId: baseId, displayVersion: 1, status: "confirmed", summary: "旧画像", modules: { profile: { title: "工程师" } }, baseContextVersionId: null, rowVersion: 1 } as const;
const draft = { ...common, contextVersionId: draftId, displayVersion: 2, status: "draft", summary: "新画像", modules: { profile: { title: "技术负责人" } }, baseContextVersionId: baseId, rowVersion: 2 } as const;
const current = { ...base, contextVersionId: currentId, displayVersion: 2, summary: "其他人已确认的新基线", baseContextVersionId: baseId } as const;
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

it("renders current context, every draft, and immutable history", async () => {
  const api = { context: vi.fn().mockResolvedValue({ current: base, history: [base], drafts: [draft, { ...draft, contextVersionId: currentId, displayVersion: 3 }] }), confirmContext: vi.fn(), compareContext: vi.fn() };
  await act(async () => root.render(<HrPositionContextPanel api={api as never} positionId={positionId} />));
  expect(container.textContent).toContain("当前已确认 v1");
  expect(container.textContent).toContain("2 个待确认草稿");
  expect(container.textContent).toContain("历史版本");
  expect(container.querySelectorAll("article[data-context-draft]")).toHaveLength(2);
});

it("reloads and renders before/after values for a changed baseline while retaining selected modules", async () => {
  const api = {
    context: vi.fn().mockResolvedValueOnce({ current: base, history: [base], drafts: [draft] }).mockResolvedValueOnce({ current, history: [current, base], drafts: [draft] }),
    confirmContext: vi.fn().mockRejectedValueOnce({ status: 409 }).mockResolvedValueOnce(current),
    compareContext: vi.fn().mockResolvedValue({ leftVersionId: baseId, rightVersionId: currentId, changedModules: ["profile"], left: { profile: { title: "工程师" } }, right: { profile: { title: "技术负责人" } } }),
  };
  await act(async () => root.render(<HrPositionContextPanel api={api as never} positionId={positionId} />));
  const checkbox = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
  await act(async () => checkbox.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "确认选中模块")?.click());
  expect(container.textContent).toContain("基线已变化");
  expect(container.textContent).toContain("画像");
  expect(container.textContent).toContain("工程师");
  expect(container.textContent).toContain("技术负责人");
  expect(checkbox.checked).toBe(true);
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "按新基线重试")?.click());
  expect(api.confirmContext).toHaveBeenLastCalledWith(positionId, draftId, currentId, ["profile"], 2, expect.any(String), expect.any(AbortSignal));
});

it("reports a confirmed context immediately and blocks every write while read-only", async () => {
  const confirmed = { ...base, contextVersionId: currentId, displayVersion: 2 };
  const api = { context: vi.fn().mockResolvedValue({ current: base, history: [base], drafts: [{ ...draft, modules: { talent_profile: { summary: "画像" }, sourcing_strategy: { summary: "搜索" } } }] }), confirmContext: vi.fn().mockResolvedValue(confirmed), compareContext: vi.fn() };
  const onConfirmed = vi.fn();
  await act(async () => root.render(<HrPositionContextPanel api={api as never} onConfirmed={onConfirmed} positionId={positionId} />));
  expect(container.textContent).toContain("人才画像");
  expect(container.textContent).toContain("搜寻策略");
  const checks = container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
  await act(async () => checks[0]?.click());
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "确认选中模块")?.click());
  expect(onConfirmed).toHaveBeenCalledWith(confirmed);

  api.confirmContext.mockClear();
  await act(async () => root.render(<HrPositionContextPanel api={api as never} onConfirmed={onConfirmed} positionId={positionId} readOnly />));
  expect([...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].every((item) => item.matches(":disabled"))).toBe(true);
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "确认选中模块")?.click());
  expect(api.confirmContext).not.toHaveBeenCalled();
});

it("opens history in an accessible focusable drawer", async () => {
  const api = { context: vi.fn().mockResolvedValue({ current: base, history: [base], drafts: [] }), confirmContext: vi.fn(), compareContext: vi.fn() };
  await act(async () => root.render(<HrPositionContextPanel api={api as never} positionId={positionId} />));
  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent?.includes("历史版本"))?.click());
  const drawer = container.querySelector('[role="dialog"][aria-modal="true"]');
  expect(drawer?.getAttribute("aria-label")).toBe("岗位上下文历史版本");
  expect(document.activeElement?.getAttribute("aria-label")).toBe("关闭历史版本");
});
