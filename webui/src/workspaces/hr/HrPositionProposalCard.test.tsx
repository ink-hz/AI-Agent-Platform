/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrPositionPackage } from "../../hrTypes";
import { HrPositionProposalCard } from "./HrPositionProposalCard";


const positionPackage = {
  draftId: "11111111-1111-4111-8111-111111111111",
  draftVersionId: "22222222-2222-4222-8222-222222222222",
  conversationId: "33333333-3333-4333-8333-333333333333",
  versionNumber: 2,
  title: "高级/视觉算法工程师",
  modules: {
    mission: { text: "负责空间视觉算法落地" },
    jd: { text: "# 对外 JD\n\n建设视觉算法产品。" },
    jr: { text: "# 内部 JR\n\n需要多视图几何经验。" },
  },
  rowVersion: 3,
  createdAt: "2026-09-04T01:00:00Z",
  updatedAt: "2026-09-04T02:00:00Z",
} satisfies HrPositionPackage;


describe("HrPositionProposalCard", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows the three business tabs and copies only the selected visible module", async () => {
    const copy = vi.fn().mockResolvedValue(true);
    const withHiddenEnvelope = { ...positionPackage, hiddenEnvelope: "position_package:secret" } as HrPositionPackage;
    await act(async () => root.render(<HrPositionProposalCard
      onConfirm={vi.fn()} onCopy={copy} positionPackage={withHiddenEnvelope}
    />));

    expect(container.querySelector("h2")?.textContent).toBe("岗位方案");
    expect([...container.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent))
      .toEqual(["岗位需求", "JD", "JR"]);
    expect(container.textContent).toContain("负责空间视觉算法落地");
    expect(container.textContent).not.toContain("position_package:secret");

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "JD")?.click());
    expect(container.textContent).toContain("建设视觉算法产品");
    const copyButton = container.querySelector<HTMLButtonElement>('[aria-label="复制 JD"]')!;
    expect(copyButton.querySelector("svg")).not.toBeNull();
    await act(async () => copyButton.click());
    expect(copy).toHaveBeenCalledWith(positionPackage.modules.jd.text);
    expect(container.querySelector('[aria-label="已复制 JD"]')).not.toBeNull();
  });

  it("does not attribute a delayed copy result to a different tab", async () => {
    let finishCopy: ((copied: boolean) => void) | undefined;
    const copy = vi.fn(() => new Promise<boolean>((resolve) => { finishCopy = resolve; }));
    await act(async () => root.render(<HrPositionProposalCard
      onConfirm={vi.fn()} onCopy={copy} positionPackage={positionPackage}
    />));

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "JD")?.click());
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="复制 JD"]')?.click());
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((button) => button.textContent === "JR")?.click());
    await act(async () => finishCopy?.(true));

    expect(copy).toHaveBeenCalledWith(positionPackage.modules.jd.text);
    expect(container.querySelector('[aria-label="已复制 JR"]')).toBeNull();
    expect(container.querySelector('[aria-label="复制 JR"]')).not.toBeNull();
  });

  it("moves through package tabs with Arrow, Home, and End keys", async () => {
    await act(async () => root.render(<HrPositionProposalCard
      onConfirm={vi.fn()} positionPackage={positionPackage}
    />));
    const tabs = [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    tabs[0].focus();

    await act(async () => tabs[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(document.activeElement).toBe(tabs[1]);
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    await act(async () => tabs[1].dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true })));
    expect(document.activeElement).toBe(tabs[2]);
    await act(async () => tabs[2].dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true })));
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("downloads the selected module as a usable, safely named file and revokes its object URL", async () => {
    vi.useFakeTimers();
    const createObjectURL = vi.fn().mockReturnValue("blob:position-package");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    let clickedAnchor: HTMLAnchorElement | null = null;
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function capture(this: HTMLAnchorElement) {
      clickedAnchor = this;
    });
    await act(async () => root.render(<HrPositionProposalCard
      onConfirm={vi.fn()} positionPackage={positionPackage}
    />));

    const download = container.querySelector<HTMLButtonElement>('[aria-label="下载岗位需求"]')!;
    expect(download.querySelector("svg")).not.toBeNull();
    await act(async () => download.click());

    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledTimes(1);
    const captured = clickedAnchor as HTMLAnchorElement | null;
    expect(captured?.download).toBe("高级-视觉算法工程师-岗位需求.md");
    expect(captured?.href).toBe("blob:position-package");
    expect(revokeObjectURL).not.toHaveBeenCalled();
    await act(async () => vi.runAllTimers());
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:position-package");
    expect(document.querySelector('a[download="高级-视觉算法工程师-岗位需求.md"]')).toBeNull();
  });

  it("accepts only one confirmation while the first request is in flight", async () => {
    let settle: (() => void) | undefined;
    const onConfirm = vi.fn(() => new Promise<void>((resolve) => { settle = resolve; }));
    await act(async () => root.render(<HrPositionProposalCard
      onConfirm={onConfirm} positionPackage={positionPackage}
    />));
    const confirm = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认并加入岗位库")!;

    await act(async () => { confirm.click(); confirm.click(); });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(confirm.disabled).toBe(true);
    expect(confirm.textContent).toBe("正在加入岗位库…");
    await act(async () => settle?.());
    expect(confirm.disabled).toBe(false);
  });
});
