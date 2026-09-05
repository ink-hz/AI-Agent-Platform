/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrOfficialPositionVersion } from "../../hrR12Types";
import { HrOfficialPositionPanel } from "./HrOfficialPositionPanel";

const POSITION_ID = "11111111-1111-4111-8111-111111111111";
const CURRENT_ID = "22222222-2222-4222-8222-222222222222";
const OLD_ID = "33333333-3333-4333-8333-333333333333";

function version(officialVersionId: string, sourceVersion: string, duty: string): HrOfficialPositionVersion {
  return {
    officialVersionId, positionId: POSITION_ID, officialJobId: "JOBAD:511189335",
    title: "DQE工程师（数采设备）", department: "质量部", locations: ["广东省·深圳市"],
    category: "质量", subcategory: null, headcount: 0, degree: null, employmentType: "全职",
    salary: "官网未公开", duty, requirement: "熟悉 DQE 方法", officialStatus: "active",
    statusReason: "published", sourceVersion, sourceChangedAt: "2026-09-05T01:00:00Z",
    sourceSnapshotAt: "2026-09-05T02:00:00Z", contentHash: "a".repeat(64),
    firstObservedAt: "2026-09-04T01:00:00Z", lastObservedAt: "2026-09-05T02:00:00Z",
    consecutiveMisses: 0, officialStatusCode: 1, createdAt: "2026-09-05T02:00:00Z",
  };
}

describe("HrOfficialPositionPanel", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("shows the complete current official source, honest missing fields, and immutable history", async () => {
    const current = version(CURRENT_ID, "sync-v2", "负责数采设备质量策划");
    const old = version(OLD_ID, "sync-v1", "历史岗位职责");
    const api = {
      officialVersions: vi.fn().mockResolvedValue([current, old]),
      officialVersion: vi.fn().mockImplementation((_positionId, id) => Promise.resolve(id === OLD_ID ? old : current)),
      downloadOfficialVersion: vi.fn(),
    };
    await act(async () => root.render(<HrOfficialPositionPanel
      api={api as never} currentSourceVersion="sync-v2" positionId={POSITION_ID}
    />));

    expect(container.querySelector("h3")?.textContent).toBe("官网岗位原文");
    expect(container.textContent).toContain("负责数采设备质量策划");
    expect(container.textContent).toContain("熟悉 DQE 方法");
    expect(container.textContent).toContain("官网未公开");
    expect(container.textContent).toContain("历史版本（2）");

    const selector = container.querySelector<HTMLSelectElement>('select[aria-label="官网岗位历史版本"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(selector, OLD_ID);
      selector.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(api.officialVersion).toHaveBeenCalledWith(POSITION_ID, OLD_ID, expect.any(AbortSignal));
    expect(container.textContent).toContain("历史岗位职责");
  });
});
