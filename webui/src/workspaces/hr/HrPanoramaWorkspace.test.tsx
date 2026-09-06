/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { HrPanoramaApiError, type HrPanoramaApi } from "../../hrPanoramaApi";
import type { HrPanoramaInsight, HrPanoramaReport, HrPanoramaSource } from "../../hrPanoramaTypes";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";

const account: Account = {
  internal_user_id: "member", display_name: "磐德", role: "member", departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false,
  csrf_token: "csrf",
};
const source: HrPanoramaSource = {
  sourceId: "11111111-1111-4111-8111-111111111111", sourceKind: "company", canonicalName: "联合光电",
  aliases: ["中山联合光电"], approvedUrls: ["https://www.union-optech.com/jobs"], active: true,
  createdAt: "2026-09-04T08:00:00Z", updatedAt: "2026-09-05T08:00:00Z",
};
const insight: HrPanoramaInsight = {
  insightVersionId: "55555555-5555-4555-8555-555555555555",
  runId: null, productionBatchId: "33333333-3333-4333-8333-333333333333", versionNumber: 2,
  selectedSourceIds: [source.sourceId], snapshotIds: ["66666666-6666-4666-8666-666666666666"],
  facts: [{ factId: "fact-1", text: "联合光电公开招聘光学结构工程师", snapshotId: "66666666-6666-4666-8666-666666666666",
    observationId: "77777777-7777-4777-8777-777777777777", sourceUrl: "https://www.union-optech.com/jobs/1", observedAt: "2026-09-05T08:00:00Z" }],
  inferences: [{ text: "光学与结构能力正在形成组合投入", basisFactIds: ["fact-1"] }],
  unknowns: [{ text: "实际 HC 未公开" }], directionClusters: { 光学: 1, 结构: 1 },
  summary: "光学与结构研发招聘保持投入", sourceConversationId: null,
  sourceTurnId: null, agentId: "hr-intelligence-producer",
  modelVersion: "configured-model-v1", createdAt: "2026-09-05T09:00:00Z",
};
const report: HrPanoramaReport = {
  publication: {
    publicationId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    batchId: insight.productionBatchId!,
    insightVersionId: insight.insightVersionId,
    coverageState: "complete",
    sourceCoverage: [{ sourceId: source.sourceId, state: "succeeded", observedAt: "2026-09-05T08:00:00Z", sourceUrls: source.approvedUrls, jobCount: 1 }],
    publishedAt: "2026-09-05T09:05:00Z",
  },
  insight, sources: [source], snapshots: [{
    snapshotId: insight.snapshotIds[0], runId: null, productionBatchId: insight.productionBatchId, observationId: insight.facts[0].observationId, sourceId: source.sourceId, publicJobKey: "optics-structure-1",
    title: "光学结构工程师", location: "中山", dutyExcerpt: "负责光学产品结构研发",
    requirementExcerpt: "五年以上精密结构经验", sourceUrl: insight.facts[0].sourceUrl,
    observedAt: insight.facts[0].observedAt, contentSha256: "a".repeat(64), status: "open",
    createdAt: "2026-09-05T08:01:00Z",
  }],
  evidence: [{ sourceId: source.sourceId, sourceUrl: source.approvedUrls[0], attemptNumber: 1, state: "succeeded", errorCode: null, sha256: "a".repeat(64), mime: "text/html", sizeBytes: 1024, normalizedJobCount: 1, observedAt: insight.facts[0].observedAt }],
};

function fakeApi(overrides: Partial<HrPanoramaApi> = {}): HrPanoramaApi {
  return {
    currentReport: vi.fn().mockResolvedValue(report),
    listReports: vi.fn().mockResolvedValue([{ publication: report.publication, insight }]),
    report: vi.fn().mockResolvedValue(report),
    ...overrides,
  };
}

async function settle(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe("HrPanoramaWorkspace", () => {
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
    vi.restoreAllMocks();
  });

  it("shows published analysis and raw jobs without collection controls", async () => {
    const api = fakeApi();
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await settle();

    expect(api.currentReport).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(container.textContent).toContain("AI 分析");
    expect(container.textContent).toContain("原始岗位数据");
    expect(container.textContent).toContain("数据截至");
    expect(container.textContent).toContain("光学与结构研发招聘保持投入");
    expect(container.textContent).not.toContain("立即更新");
    expect(container.textContent).not.toContain("添加关注公司");
    expect(container.textContent).not.toContain("正在收集公开招聘岗位");
  });

  it("opens a historical deep link without loading current", async () => {
    const historical = { ...report, insight: { ...insight, summary: "历史版本分析" } };
    const api = fakeApi({ report: vi.fn().mockResolvedValue(historical) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} insightVersionId={report.publication.publicationId} />));
    await settle();

    expect(api.currentReport).not.toHaveBeenCalled();
    expect(api.report).toHaveBeenCalledWith(report.publication.publicationId, expect.any(AbortSignal));
    expect(container.textContent).toContain("历史版本分析");
  });

  it("keeps a valid report readable when history is unavailable", async () => {
    const api = fakeApi({ listReports: vi.fn().mockRejectedValue(new Error("offline")) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await settle();

    expect(container.textContent).toContain("光学与结构研发招聘保持投入");
    expect(container.textContent).not.toContain("招聘情报暂时无法读取");
  });

  it("shows a read-only empty state while the first publication is prepared", async () => {
    const api = fakeApi({ currentReport: vi.fn().mockResolvedValue(null), listReports: vi.fn().mockResolvedValue([]) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await settle();

    expect(container.textContent).toContain("首份招聘情报正在后台准备");
    expect(container.textContent).toContain("无需手动发起");
    expect(container.querySelector("button")).toBeNull();
  });

  it("turns read failures into a retry of reads only", async () => {
    const currentReport = vi.fn()
      .mockRejectedValueOnce(new HrPanoramaApiError(503))
      .mockResolvedValueOnce(report);
    const api = fakeApi({ currentReport });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await settle();
    expect(container.textContent).toContain("招聘情报暂时无法读取");

    await act(async () => container.querySelector<HTMLButtonElement>("button")?.click());
    await settle();
    expect(currentReport).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("光学与结构研发招聘保持投入");
  });

  it("loads the nearest previous report with the same company scope", async () => {
    const previous = { ...insight, insightVersionId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", versionNumber: 1 };
    const previousPublication = { ...report.publication, publicationId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", insightVersionId: previous.insightVersionId };
    const otherScope = { ...previous, insightVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", selectedSourceIds: ["22222222-2222-4222-8222-222222222222"] };
    const otherPublication = { ...previousPublication, publicationId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", insightVersionId: otherScope.insightVersionId };
    const previousReport = { ...report, publication: previousPublication, insight: previous };
    const detail = vi.fn().mockImplementation(async (id: string) => id === previousPublication.publicationId ? previousReport : report);
    const api = fakeApi({ listReports: vi.fn().mockResolvedValue([{ publication: report.publication, insight }, { publication: otherPublication, insight: otherScope }, { publication: previousPublication, insight: previous }]), report: detail });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await settle();

    expect(detail).toHaveBeenCalledWith(previousPublication.publicationId, expect.any(AbortSignal));
    expect(detail).not.toHaveBeenCalledWith(otherPublication.publicationId, expect.anything());
  });
});
