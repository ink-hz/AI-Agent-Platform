/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrPositionDetail } from "../../hrTypes";
import { HrPositionHeader } from "./HrPositionHeader";

const detail: HrPositionDetail = {
  positionId: "11111111-1111-4111-8111-111111111111",
  sourceKind: "official_site",
  officialJobId: "J11014",
  title: "3D 打印高级结构工程师",
  department: "研发",
  locations: ["深圳", "中山"],
  officialStatus: "active",
  internalStatus: "active",
  sourceVersion: "sync-v2",
  rowVersion: 2,
  createdAt: "2026-09-01T00:00:00Z",
  updatedAt: "2026-09-04T00:00:00Z",
  conversationCount: 2,
  materialCount: 1,
  artifactCount: 3,
  conversationIds: [],
  materialAttachmentIds: [],
  artifactIds: [],
  artifactAttachmentIds: [],
};

describe("HrPositionHeader", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("keeps the position bar compact and exposes only primary actions", async () => {
    const onOpenDetails = vi.fn();
    const onNewConversation = vi.fn();
    await act(async () => root.render(<HrPositionHeader
      detail={detail} onNewConversation={onNewConversation}
      onOpenDetails={onOpenDetails} readOnly={false}
    />));

    expect(container.textContent).toContain("3D 打印高级结构工程师");
    expect(container.textContent).toContain("研发 · 深圳 · 中山");
    expect(container.textContent).toContain("进行中");
    expect(container.textContent).not.toContain("J11014");
    expect(container.textContent).not.toContain("2 个对话");
    expect(container.textContent).not.toContain("1 份岗位材料");
    expect(container.textContent).not.toContain("3 个生成结果");

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "岗位资料")?.click());
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "＋ 新对话")?.click());
    expect(onOpenDetails).toHaveBeenCalledTimes(1);
    expect(onNewConversation).toHaveBeenCalledTimes(1);
  });

  it("disables new conversation in read-only mode", async () => {
    await act(async () => root.render(<HrPositionHeader
      detail={detail} onNewConversation={vi.fn()} onOpenDetails={vi.fn()} readOnly
    />));
    expect([...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "＋ 新对话")?.disabled).toBe(true);
  });
});
