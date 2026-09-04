/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrPositionDetail } from "../../hrTypes";
import { HrPositionDetailsDrawer } from "./HrPositionDetailsDrawer";

const POSITION_ID = "11111111-1111-4111-8111-111111111111";
const detail: HrPositionDetail = {
  positionId: POSITION_ID, sourceKind: "official_site", officialJobId: "J11014",
  title: "高级结构工程师", department: "研发", locations: ["深圳"],
  officialStatus: "active", internalStatus: "active", sourceVersion: "sync-v2", rowVersion: 2,
  createdAt: "2026-09-01T00:00:00Z", updatedAt: "2026-09-04T00:00:00Z",
  conversationCount: 0, materialCount: 0, artifactCount: 0,
  conversationIds: [], materialAttachmentIds: [], artifactIds: [], artifactAttachmentIds: [],
};

function api() {
  return {
    context: vi.fn().mockResolvedValue({ current: null, drafts: [], history: [] }),
    compareContext: vi.fn(), confirmContext: vi.fn(),
    candidateDrafts: vi.fn().mockResolvedValue([]), positionCandidates: vi.fn().mockResolvedValue([]),
    candidate: vi.fn(), candidateDocuments: vi.fn(), candidateAnalyses: vi.fn(), candidateFeedback: vi.fn(),
    retryDraft: vi.fn(), confirmDraft: vi.fn(), createCandidateDraftBatch: vi.fn(),
    appendCandidateFeedback: vi.fn(), compareCandidates: vi.fn(), downloadCandidateDocument: vi.fn(),
    startTask: vi.fn(), taskStatus: vi.fn(),
    resources: vi.fn().mockResolvedValue({ materials: [], artifacts: [] }), downloadResource: vi.fn(),
  };
}

describe("HrPositionDetailsDrawer", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("loads business tabs only after they are opened and uses normal empty states", async () => {
    const client = api();
    await act(async () => root.render(<HrPositionDetailsDrawer
      api={client as never} csrfToken="csrf" detail={detail} open
      onClose={vi.fn()} onConfirmed={vi.fn()} readOnly={false}
    />));

    expect(container.querySelector('[role="dialog"][aria-label="岗位资料"]')).not.toBeNull();
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("岗位信息");
    expect(container.textContent).toContain("当前岗位理解");
    expect(container.textContent).toContain("官网同步");
    expect(client.positionCandidates).not.toHaveBeenCalled();
    expect(client.resources).not.toHaveBeenCalled();

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "候选人")?.click());
    expect(client.positionCandidates).toHaveBeenCalledWith(POSITION_ID, expect.any(AbortSignal));
    expect(container.textContent).toContain("暂无候选人");

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "材料与成果")?.click());
    expect(client.resources).toHaveBeenCalledWith(POSITION_ID, expect.any(AbortSignal));
    expect(container.textContent).toContain("暂无岗位材料");
    expect(container.textContent).toContain("暂无生成成果");
  });

  it("supports initial and controlled active tabs for legacy position routes", async () => {
    const client = api();
    const onActiveTabChange = vi.fn();
    await act(async () => root.render(<HrPositionDetailsDrawer
      api={client as never} csrfToken="csrf" detail={detail} initialTab="resources" open
      onActiveTabChange={onActiveTabChange} onClose={vi.fn()} onConfirmed={vi.fn()} readOnly={false}
    />));

    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("材料与成果");
    expect(client.resources).toHaveBeenCalledWith(POSITION_ID, expect.any(AbortSignal));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "候选人")?.click());
    expect(onActiveTabChange).toHaveBeenCalledWith("candidates");
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("候选人");

    await act(async () => root.render(<HrPositionDetailsDrawer
      activeTab="resources" api={client as never} csrfToken="csrf" detail={detail} open
      onActiveTabChange={onActiveTabChange} onClose={vi.fn()} onConfirmed={vi.fn()} readOnly={false}
    />));
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("材料与成果");
  });

  it("closes on Escape and returns focus to the element that opened it", async () => {
    const opener = document.createElement("button"); opener.textContent = "岗位资料"; document.body.append(opener); opener.focus();
    const onClose = vi.fn();
    await act(async () => root.render(<HrPositionDetailsDrawer
      api={api() as never} csrfToken="csrf" detail={detail} open
      onClose={onClose} onConfirmed={vi.fn()} readOnly={false}
    />));
    const dialog = container.querySelector<HTMLElement>('[role="dialog"][aria-label="岗位资料"]')!;
    await act(async () => dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("keeps Shift+Tab inside the active panel after another panel has been visited", async () => {
    await act(async () => root.render(<HrPositionDetailsDrawer
      api={api() as never} csrfToken="csrf" detail={detail} open
      onClose={vi.fn()} onConfirmed={vi.fn()} readOnly={false}
    />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "候选人")?.click());
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "岗位信息")?.click());

    const dialog = container.querySelector<HTMLElement>('[role="dialog"][aria-label="岗位资料"]')!;
    const activePanel = dialog.querySelector<HTMLElement>('[role="tabpanel"]:not([hidden])')!;
    const activeButtons = [...activePanel.querySelectorAll<HTMLButtonElement>("button:not([disabled])")];
    const expectedLast = activeButtons[activeButtons.length - 1];
    const close = dialog.querySelector<HTMLButtonElement>('[aria-label="关闭岗位资料"]')!;
    close.focus();
    await act(async () => dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true })));

    expect(expectedLast).toBeDefined();
    expect(document.activeElement).toBe(expectedLast);
  });
});
