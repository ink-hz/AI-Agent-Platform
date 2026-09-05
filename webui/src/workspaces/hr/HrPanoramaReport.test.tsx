/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrPanoramaReport as Report } from "../../hrPanoramaTypes";
import { HrPanoramaReport } from "./HrPanoramaReport";

const report: Report = {
  insight: {
    insightVersionId: "55555555-5555-4555-8555-555555555555", runId: "33333333-3333-4333-8333-333333333333",
    versionNumber: 2, selectedSourceIds: ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"],
    snapshotIds: ["66666666-6666-4666-8666-666666666666", "77777777-7777-4777-8777-777777777777"],
    facts: [{ factId: "f1", text: "联合光电公开招聘结构工程师", snapshotId: "66666666-6666-4666-8666-666666666666",
      observationId: "88888888-8888-4888-8888-888888888888", sourceUrl: "https://example.com/jobs/1", observedAt: "2026-09-05T08:00:00Z" }],
    inferences: [{ text: "精密结构人才投入可能增加", basisFactIds: ["f1"] }],
    unknowns: [{ text: "实际招聘人数仍待确认" }], directionClusters: { 精密结构: 4, 光学设计: 2 },
    summary: "两家公司持续布局光学与精密结构人才。", sourceConversationId: "44444444-4444-4444-8444-444444444444",
    sourceTurnId: "99999999-9999-4999-8999-999999999999", agentId: "hr-bot", modelVersion: "gpt-5",
    createdAt: "2026-09-05T09:00:00Z",
  },
  sources: [
    { sourceId: "11111111-1111-4111-8111-111111111111", sourceKind: "company", canonicalName: "联合光电", aliases: [], approvedUrls: ["https://example.com/jobs"], active: true, createdAt: "2026-09-04T08:00:00Z", updatedAt: "2026-09-05T08:00:00Z" },
    { sourceId: "22222222-2222-4222-8222-222222222222", sourceKind: "company", canonicalName: "舜宇光学", aliases: [], approvedUrls: ["https://sunny.example/jobs"], active: true, createdAt: "2026-09-04T08:00:00Z", updatedAt: "2026-09-05T08:00:00Z" },
  ],
  snapshots: [
    { snapshotId: "66666666-6666-4666-8666-666666666666", runId: "33333333-3333-4333-8333-333333333333", sourceId: "11111111-1111-4111-8111-111111111111", publicJobKey: "job-1", title: "结构工程师", location: "中山", dutyExcerpt: "负责精密结构设计", requirementExcerpt: "五年以上光学行业经验", sourceUrl: "https://example.com/jobs/1", observedAt: "2026-09-05T08:00:00Z", contentSha256: "a".repeat(64), status: "open", createdAt: "2026-09-05T08:01:00Z" },
    { snapshotId: "77777777-7777-4777-8777-777777777777", runId: "33333333-3333-4333-8333-333333333333", sourceId: "22222222-2222-4222-8222-222222222222", publicJobKey: "job-2", title: "光学工程师", location: "宁波", dutyExcerpt: "负责光学系统设计", requirementExcerpt: "熟悉 Zemax", sourceUrl: "https://sunny.example/jobs/2", observedAt: "2026-09-05T08:05:00Z", contentSha256: "b".repeat(64), status: "open", createdAt: "2026-09-05T08:06:00Z" },
  ],
};
const previousReport: Report = {
  ...report,
  insight: { ...report.insight, insightVersionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", versionNumber: 1, snapshotIds: [report.snapshots[0].snapshotId, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"], createdAt: "2026-09-04T09:00:00Z" },
  snapshots: [
    report.snapshots[0],
    { ...report.snapshots[0], snapshotId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", publicJobKey: "job-offline", title: "已下线岗位", sourceUrl: "https://example.com/jobs/offline" },
  ],
};

describe("HrPanoramaReport", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
  afterEach(async () => {
    await act(async () => root.unmount()); container.remove();
    delete (URL as { createObjectURL?: unknown }).createObjectURL;
    delete (URL as { revokeObjectURL?: unknown }).revokeObjectURL;
    vi.restoreAllMocks();
  });

  it("renders a readable multi-company report with evidence classes kept separate", async () => {
    await act(async () => root.render(<HrPanoramaReport comparison={{ state: "available", previousReport, currentSourceFailures: {}, previousSourceFailures: {} }} report={report} />));

    expect(container.querySelector("h1")?.textContent).toContain("两家公司");
    expect(container.textContent).toContain("联合光电");
    expect(container.textContent).toContain("舜宇光学");
    expect(container.textContent).toContain("研发方向");
    expect(container.textContent).toContain("招聘变化");
    expect(container.textContent).toContain("新增岗位");
    expect(container.textContent).toContain("明确关闭");
    expect(container.textContent).toContain("持续招聘");
    expect(container.textContent).toContain("地域分布");
    expect(container.textContent).toContain("关键能力");
    expect(container.textContent).toContain("重点团队与投入信号");
    expect(container.textContent).toContain("精密结构人才投入可能增加");
    const evidenceTab = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "来源证据");
    await act(async () => evidenceTab?.click());
    expect(container.querySelector('[data-evidence-kind="facts"] h2')?.textContent).toBe("公开事实");
    expect(container.querySelector('[data-evidence-kind="inferences"] h2')?.textContent).toBe("AI 推断");
    expect(container.querySelector('[data-evidence-kind="unknowns"] h2')?.textContent).toBe("仍待确认");
    expect(container.querySelector<HTMLAnchorElement>('a[href="https://example.com/jobs/1"]')?.textContent).toContain("查看公开来源");
    const inference = container.querySelector('[data-evidence-kind="inferences"]');
    expect(inference?.querySelector<HTMLAnchorElement>('a[href="https://example.com/jobs/1"]')?.textContent).toContain("联合光电公开招聘结构工程师");
    expect(inference?.querySelector('time[datetime="2026-09-05T08:00:00Z"]')).not.toBeNull();
    expect(container.querySelector('time[datetime="2026-09-05T08:00:00Z"]')).not.toBeNull();
    expect(container.querySelector("details")?.open).toBe(false);
  });

  it("shows an auditable intelligence source matrix for every company", async () => {
    await act(async () => root.render(<HrPanoramaReport report={report} />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "来源证据")?.click());

    const matrix = container.querySelector('[data-evidence-kind="source-matrix"]');
    expect(matrix?.textContent).toContain("情报来源矩阵");
    expect(matrix?.textContent).toContain("联合光电");
    expect(matrix?.textContent).toContain("舜宇光学");
    expect(matrix?.textContent).toContain("已观测 1 条岗位");
    expect(matrix?.querySelector<HTMLAnchorElement>('a[href="https://example.com/jobs"]')).not.toBeNull();
    expect(matrix?.querySelector<HTMLAnchorElement>('a[href="https://sunny.example/jobs"]')).not.toBeNull();
  });

  it("separates social and campus recruiting without forcing unmarked jobs into either track", async () => {
    const tracked: Report = {
      ...report,
      snapshots: [
        { ...report.snapshots[0], sourceUrl: "https://example.com/experienced/job-1" },
        { ...report.snapshots[1], title: "2027届校园招聘｜光学工程师", sourceUrl: "https://sunny.example/campus/job-2" },
        { ...report.snapshots[0], snapshotId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", publicJobKey: "job-3", title: "算法工程师", sourceUrl: "https://example.com/jobs/3" },
      ],
    };
    await act(async () => root.render(<HrPanoramaReport report={tracked} />));

    expect(container.textContent).toContain("总览");
    expect(container.textContent).toContain("社招");
    expect(container.textContent).toContain("校招");
    expect(container.textContent).toContain("产品与业务方向");
    expect(container.textContent).toContain("岗位明细");
    expect(container.textContent).toContain("来源证据");

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "校招")?.click());
    const campus = container.querySelector('[data-report-view="campus"]');
    expect(campus?.textContent).toContain("2027届校园招聘｜光学工程师");
    expect(campus?.textContent).not.toContain("结构工程师");
    expect(campus?.textContent).toContain("未识别到校招标记，待确认");

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "社招")?.click());
    const social = container.querySelector('[data-report-view="social"]');
    expect(social?.textContent).toContain("结构工程师");
    expect(social?.textContent).not.toContain("2027届校园招聘｜光学工程师");
    expect(social?.textContent).toContain("1 个岗位尚未识别招聘类型");
  });

  it("filters job details by company, recruiting type, location, status, and technical direction", async () => {
    await act(async () => root.render(<HrPanoramaReport report={report} />));
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "岗位明细")?.click());

    for (const label of ["公司", "招聘类型", "地点", "岗位状态", "技术方向"]) {
      expect(container.querySelector(`select[aria-label="${label}"]`)).not.toBeNull();
    }
    const company = container.querySelector<HTMLSelectElement>('select[aria-label="公司"]');
    await act(async () => {
      if (!company) return;
      company.value = report.sources[0].sourceId;
      company.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const jobs = container.querySelector('[data-report-view="jobs"]');
    expect(jobs?.textContent).toContain("结构工程师");
    expect(jobs?.textContent).not.toContain("光学工程师");
    expect(jobs?.textContent).toContain("1 / 2 条");
  });

  it("labels a report without a prior same-scope version as the first baseline", async () => {
    await act(async () => root.render(<HrPanoramaReport comparison={{ state: "none", currentSourceFailures: {} }} report={report} />));
    expect(container.textContent).toContain("首次分析，暂无变化基线");
  });

  it("does not call an unavailable comparison baseline the first analysis", async () => {
    await act(async () => root.render(<HrPanoramaReport comparison={{ state: "unavailable" }} report={report} />));
    expect(container.textContent).toContain("变化基线暂时不可用，当前报告仍可查看");
    expect(container.textContent).not.toContain("首次分析");
  });

  it("does not call a missing job from a failed source an offline role", async () => {
    const partial = {
      ...report,
      insight: { ...report.insight, snapshotIds: [report.snapshots[1].snapshotId] },
      snapshots: [report.snapshots[1]],
    };
    await act(async () => root.render(<HrPanoramaReport comparison={{
      state: "available", previousReport,
      currentSourceFailures: { [report.sources[0].sourceId]: "search_unavailable" }, previousSourceFailures: {},
    }} report={partial} />));

    const changes = [...container.querySelectorAll(".hr-panorama-signal-grid section")].find((section) => section.textContent?.includes("招聘变化"));
    expect(changes?.textContent).toContain("明确关闭0");
    expect(changes?.textContent).toContain("联合光电本轮采集失败，无法判断变化");
  });

  it("does not treat an unknown current status as an explicit closure", async () => {
    const unknown = {
      ...report,
      insight: { ...report.insight, snapshotIds: [report.snapshots[0].snapshotId] },
      snapshots: [{ ...report.snapshots[0], publicJobKey: "job-offline", status: "unknown" as const }],
    };
    await act(async () => root.render(<HrPanoramaReport comparison={{
      state: "available", previousReport, currentSourceFailures: {}, previousSourceFailures: {},
    }} report={unknown} />));

    const changes = [...container.querySelectorAll(".hr-panorama-signal-grid section")].find((section) => section.textContent?.includes("招聘变化"));
    expect(changes?.textContent).toContain("明确关闭0");
  });

  it("counts only an explicit closed snapshot as a confirmed closure", async () => {
    const closed = {
      ...report,
      insight: { ...report.insight, snapshotIds: [report.snapshots[0].snapshotId] },
      snapshots: [{ ...report.snapshots[0], publicJobKey: "job-offline", status: "closed" as const }],
    };
    await act(async () => root.render(<HrPanoramaReport comparison={{
      state: "available", previousReport, currentSourceFailures: {}, previousSourceFailures: {},
    }} report={closed} />));

    const changes = [...container.querySelectorAll(".hr-panorama-signal-grid section")].find((section) => section.textContent?.includes("招聘变化"));
    expect(changes?.textContent).toContain("明确关闭1");
  });

  it("copies and downloads a deterministic readable Markdown report", async () => {
    const copy = vi.fn().mockResolvedValue(true);
    const createObjectURL = vi.fn().mockReturnValue("blob:panorama");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    let downloaded = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloaded = this.download; });
    await act(async () => root.render(<HrPanoramaReport comparison={{ state: "available", previousReport, currentSourceFailures: {}, previousSourceFailures: {} }} onCopy={copy} report={report} />));

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent?.includes("复制报告"))?.click());
    const copied = String(copy.mock.calls[0]?.[0]);
    expect(copied).toContain("# 全景分析 · 第 2 版");
    expect(copied).toContain("新增岗位：1");
    expect(copied).toContain("明确关闭：0");
    expect(copied).toContain("本次未再次采集到（待验证，不代表停止招聘）：1");
    expect(copied).toContain("## 公开事实");
    expect(copied).toContain("## 地域分布");
    expect(copied).toContain("中山：1 个岗位");
    expect(copied).toContain("## 关键能力");
    expect(copied).toContain("联合光电｜结构工程师：五年以上光学行业经验");
    expect(copied).toContain("联合光电｜结构工程师｜公开招聘中｜中山");
    expect(copied).toContain("职责：负责精密结构设计");
    expect(copied).toContain("要求：五年以上光学行业经验");
    expect(copied).toContain("https://example.com/jobs/1");
    expect(copied).toContain("观测于 2026-09-05T08:00:00Z");

    expect(container.querySelector<HTMLAnchorElement>('a[href*="/export?format=pdf"]')?.textContent).toBe("下载 PDF");
    expect(container.querySelector<HTMLAnchorElement>('a[href*="/export?format=xlsx"]')?.textContent).toBe("下载 Excel");
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "下载 Markdown")?.click());
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    const downloadedText = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsText(createObjectURL.mock.calls[0][0] as Blob);
    });
    expect(downloadedText).toBe(copied);
    expect(downloaded).toBe("全景分析-第2版-2026-09-05.md");
    await act(async () => new Promise((resolve) => window.setTimeout(resolve, 0)));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:panorama");
  });
});
