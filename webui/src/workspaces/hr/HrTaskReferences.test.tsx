/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { HrTaskReferences } from "./HrTaskReferences";

describe("HrTaskReferences", () => {
  let host: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it("keeps safe task references collapsed and shows readable provenance", async () => {
    await act(async () => root.render(<HrTaskReferences references={[{
      sourceType: "official_position",
      sourceId: "11111111-1111-4111-8111-111111111111",
      displayLabel: "官网岗位 · sync-v2",
      version: "sync-v2",
      selectedReason: "岗位任务的官网基线",
      freshness: "2026-09-05",
    }, {
      sourceType: "panorama_insight",
      sourceId: "22222222-2222-4222-8222-222222222222",
      displayLabel: "全景招聘情报 · 截至 2026-09-05",
      version: null,
      selectedReason: "与本岗位方向相关的招聘情报",
      freshness: "2026-09-05",
    }]} />));

    const disclosure = host.querySelector("details");
    expect(disclosure?.open).toBe(false);
    expect(disclosure?.querySelector("summary")?.textContent).toContain("本次参考 2");
    expect(host.textContent).toContain("官网岗位 · sync-v2");
    expect(host.textContent).toContain("岗位任务的官网基线");
    expect(host.textContent).toContain("全景招聘情报 · 截至 2026-09-05");
  });

  it("renders nothing when a task has no recorded references", async () => {
    await act(async () => root.render(<HrTaskReferences references={[]} />));
    expect(host.innerHTML).toBe("");
  });
});
