/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { HrPanoramaApiError, type HrPanoramaApi } from "../../hrPanoramaApi";
import type { HrPanoramaInsight, HrPanoramaReport, HrPanoramaRun, HrPanoramaSource } from "../../hrPanoramaTypes";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";

const account: Account = { internal_user_id: "member", display_name: "磐德", role: "member", departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf" };
const source: HrPanoramaSource = { sourceId: "11111111-1111-4111-8111-111111111111", sourceKind: "company", canonicalName: "联合光电", aliases: ["中山联合光电", "Union Optech"], approvedUrls: ["https://www.union-optech.com"], active: true, createdAt: "2026-09-04T08:00:00Z", updatedAt: "2026-09-05T08:00:00Z" };
const source2: HrPanoramaSource = { ...source, sourceId: "22222222-2222-4222-8222-222222222222", canonicalName: "舜宇光学", approvedUrls: ["https://sunny.example/jobs"] };
const insight: HrPanoramaInsight = { insightVersionId: "55555555-5555-4555-8555-555555555555", runId: "33333333-3333-4333-8333-333333333333", versionNumber: 1, selectedSourceIds: [source.sourceId, source2.sourceId], snapshotIds: [], facts: [], inferences: [], unknowns: [{ text: "实际 HC 未公开" }], directionClusters: { 结构: 4 }, summary: "最近一次有效报告", sourceConversationId: "44444444-4444-4444-8444-444444444444", sourceTurnId: "99999999-9999-4999-8999-999999999999", agentId: "hr-bot", modelVersion: "gpt-5", createdAt: "2026-09-05T09:00:00Z" };
const report: HrPanoramaReport = { insight, sources: [source, source2], snapshots: [] };
const runBase: HrPanoramaRun = { runId: "33333333-3333-4333-8333-333333333334", selectedSourceIds: [source.sourceId, source2.sourceId], conversationId: insight.sourceConversationId, state: "running", errorCode: null, sourceFailures: {}, rowVersion: 2, startedAt: "2026-09-05T10:00:00Z", finishedAt: null, createdAt: "2026-09-05T10:00:00Z", updatedAt: "2026-09-05T10:00:00Z" };
function completedRun(runId: string): HrPanoramaRun { return { ...runBase, runId, state: "completed", finishedAt: "2026-09-05T10:02:00Z" }; }

function fakeApi(overrides: Partial<HrPanoramaApi> = {}): HrPanoramaApi {
  return { listCompanies: vi.fn().mockResolvedValue([source, source2]), addCompany: vi.fn().mockResolvedValue(source), listReports: vi.fn().mockResolvedValue([insight]), report: vi.fn().mockResolvedValue(report), startRun: vi.fn().mockResolvedValue(runBase), runStatus: vi.fn().mockImplementation(async (runId: string) => completedRun(runId)), ...overrides };
}

describe("HrPanoramaWorkspace", () => {
  let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
  let stored: Map<string, string>;
  beforeEach(() => {
    stored = new Map();
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
      clear: () => stored.clear(), getItem: (key: string) => stored.get(key) ?? null,
      removeItem: (key: string) => stored.delete(key), setItem: (key: string, value: string) => stored.set(key, value),
    } });
    container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); delete (globalThis as { localStorage?: unknown }).localStorage; vi.useRealTimers(); vi.restoreAllMocks(); });

  it("shows followed companies, report history, and real collection progress", async () => {
    const api = fakeApi();
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} executionConversationId={insight.sourceConversationId} />));
    await act(async () => undefined);
    expect(container.textContent).toContain("关注公司");
    expect(container.textContent).toContain("联合光电");
    expect(container.textContent).toContain("分析历史");
    expect(container.textContent).toContain("分析范围：2 家");
    expect(container.querySelector(`a[href="/hr/panorama/reports/${insight.insightVersionId}"]`)).not.toBeNull();

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());
    expect(container.textContent).toContain("正在收集公开招聘岗位");
    expect(api.startRun).toHaveBeenCalledWith({ sourceIds: [source.sourceId, source2.sourceId], conversationId: insight.sourceConversationId }, expect.stringMatching(/^[0-9a-f-]{36}$/i), expect.any(AbortSignal));
  });

  it("shows the complete ten-company catalog and keeps session-derived companies as unconfirmed leads", async () => {
    const api = fakeApi();
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);

    for (const name of ["联合光电", "速腾聚创", "禾赛科技", "拓竹", "创想三维", "智能派", "知象光电", "先临三维", "思看科技", "智元机器人"]) {
      expect(container.textContent).toContain(name);
    }
    expect(container.textContent).toContain("补齐 9 家重点公司");
    expect(container.textContent).toContain("历史会话线索");
    expect(container.textContent).toContain("影石");
    expect(container.textContent).toContain("华为");
    expect(container.textContent).toContain("确认后再加入，不自动采集");
  });

  it("adds every missing catalog company once and selects the successful additions", async () => {
    let index = 10;
    const addCompany = vi.fn().mockImplementation(async (input) => ({
      ...source,
      sourceId: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index++).padStart(12, "0")}`,
      canonicalName: input.canonicalName,
      aliases: input.aliases,
      approvedUrls: input.approvedUrls,
    }));
    const api = fakeApi({ listCompanies: vi.fn().mockResolvedValue([source]), addCompany });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);

    const fill = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "补齐 9 家重点公司");
    expect(fill).toBeDefined();
    await act(async () => fill?.click());

    expect(addCompany).toHaveBeenCalledTimes(9);
    expect(new Set(addCompany.mock.calls.map(([input]) => input.canonicalName)).size).toBe(9);
    expect(container.textContent).toContain("10 家重点公司已加入");
    expect(container.textContent).toContain("分析范围：10 家");
  });

  it("does not falsely certify an existing catalog company whose approved channels are incomplete", async () => {
    const legacy = { ...source, canonicalName: "禾赛科技", aliases: ["禾赛"], approvedUrls: ["https://www.hesaitech.com/cn/careers"] };
    const api = fakeApi({ listCompanies: vi.fn().mockResolvedValue([legacy]) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);

    expect(container.textContent).toContain("已关注 · 情报来源待升级");
    expect(container.textContent).not.toContain("已关注 · 社招/校招");
  });

  it("loads a report deep link directly even when it is older than the history window", async () => {
    const oldId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const oldReport = { ...report, insight: { ...insight, insightVersionId: oldId, summary: "较早的有效报告" } };
    const api = fakeApi({ report: vi.fn().mockResolvedValue(oldReport) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} insightVersionId={oldId} />));
    await act(async () => undefined);

    expect(api.report).toHaveBeenCalledWith(oldId, expect.any(AbortSignal));
    expect(container.textContent).toContain("较早的有效报告");
  });

  it.each(["companies", "history"] as const)("keeps a valid deep-link report readable when the %s sidebar fails", async (failedSidebar) => {
    const oldId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const oldReport = { ...report, insight: { ...insight, insightVersionId: oldId, summary: "独立加载的报告" } };
    const api = fakeApi({
      ...(failedSidebar === "companies" ? { listCompanies: vi.fn().mockRejectedValue(new Error("offline")) } : {}),
      ...(failedSidebar === "history" ? { listReports: vi.fn().mockRejectedValue(new Error("offline")) } : {}),
      report: vi.fn().mockResolvedValue(oldReport),
    });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} insightVersionId={oldId} />));
    await act(async () => undefined);

    expect(container.textContent).toContain("独立加载的报告");
    expect(container.textContent).toContain(failedSidebar === "companies" ? "关注公司暂时无法读取" : "分析历史暂时无法读取");
    if (failedSidebar === "history") {
      expect(container.textContent).toContain("变化基线暂时不可用");
      expect(container.textContent).not.toContain("首次分析");
    }
  });

  it("loads only the nearest prior report with the exact same company scope for change comparison", async () => {
    const current = { ...insight, versionNumber: 3 };
    const otherScope = { ...insight, insightVersionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", versionNumber: 2, selectedSourceIds: [source.sourceId] };
    const prior = { ...insight, insightVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", versionNumber: 1 };
    const reportApi = vi.fn().mockImplementation(async (id: string) => id === prior.insightVersionId ? { ...report, insight: prior } : { ...report, insight: current });
    const api = fakeApi({ listReports: vi.fn().mockResolvedValue([current, otherScope, prior]), report: reportApi });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} insightVersionId={current.insightVersionId} />));
    await act(async () => undefined);

    expect(reportApi.mock.calls.map(([id]) => id)).toEqual([current.insightVersionId, prior.insightVersionId]);
    expect(reportApi).not.toHaveBeenCalledWith(otherScope.insightVersionId, expect.anything());
    expect(api.runStatus).toHaveBeenCalledWith(current.runId, expect.any(AbortSignal));
    expect(api.runStatus).toHaveBeenCalledWith(prior.runId, expect.any(AbortSignal));
  });

  it("keeps the current report readable when its known prior detail is unavailable", async () => {
    const current = { ...insight, versionNumber: 2 };
    const prior = { ...insight, insightVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", versionNumber: 1 };
    const reportApi = vi.fn().mockImplementation(async (id: string) => {
      if (id === prior.insightVersionId) throw new Error("offline");
      return { ...report, insight: current };
    });
    const api = fakeApi({ listReports: vi.fn().mockResolvedValue([current, prior]), report: reportApi });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} insightVersionId={current.insightVersionId} />));
    await act(async () => undefined);

    expect(container.textContent).toContain(current.summary);
    expect(container.textContent).toContain("变化基线暂时不可用");
    expect(container.textContent).not.toContain("首次分析");
  });

  it("does not claim a first analysis while a deep-link baseline is still loading", async () => {
    let resolveHistory: ((items: HrPanoramaInsight[]) => void) | undefined;
    const history = new Promise<HrPanoramaInsight[]>((resolve) => { resolveHistory = resolve; });
    const current = { ...insight, versionNumber: 2 };
    const api = fakeApi({ listReports: vi.fn().mockReturnValue(history), report: vi.fn().mockResolvedValue({ ...report, insight: current }) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} insightVersionId={current.insightVersionId} />));
    await act(async () => undefined);

    expect(container.textContent).toContain(current.summary);
    expect(container.textContent).toContain("正在核对变化基线");
    expect(container.textContent).not.toContain("首次分析");

    await act(async () => resolveHistory?.([current]));
    expect(container.textContent).toContain("首次分析，暂无变化基线");
  });

  it("does not claim a first analysis when the full history window contains only other scopes", async () => {
    const current = { ...insight, versionNumber: 200 };
    const otherScopeHistory = Array.from({ length: 100 }, (_, index) => ({
      ...insight,
      insightVersionId: `aaaaaaaa-aaaa-4aaa-8aaa-${index.toString(16).padStart(12, "0")}`,
      versionNumber: 199 - index,
      selectedSourceIds: [source.sourceId],
    }));
    const api = fakeApi({
      listReports: vi.fn().mockResolvedValue(otherScopeHistory),
      report: vi.fn().mockResolvedValue({ ...report, insight: current }),
    });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} insightVersionId={current.insightVersionId} />));
    await act(async () => undefined);

    expect(container.textContent).toContain(current.summary);
    expect(container.textContent).toContain("变化基线暂时不可用");
    expect(container.textContent).not.toContain("首次分析");
  });

  it("keeps the last valid report on partial failure and retries only the failed company", async () => {
    const partial = { ...runBase, state: "partially_completed" as const, finishedAt: "2026-09-05T10:02:00Z", sourceFailures: { [source.sourceId]: "search_unavailable" } };
    const api = fakeApi({ startRun: vi.fn().mockResolvedValue(partial) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} executionConversationId={insight.sourceConversationId} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());

    expect(container.textContent).toContain("部分公开来源暂时未能更新");
    expect(container.textContent).toContain("继续显示最近一次有效报告");
    expect(container.textContent).toContain("最近一次有效报告");
    expect(container.textContent).not.toContain("search_unavailable");
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "重试 联合光电")?.click());
    expect(api.startRun).toHaveBeenLastCalledWith({ sourceIds: [source.sourceId], conversationId: insight.sourceConversationId }, expect.any(String), expect.any(AbortSignal));
  });

  it("keeps the last valid report when the whole update fails without exposing diagnostics", async () => {
    const failed = { ...runBase, state: "failed" as const, finishedAt: "2026-09-05T10:02:00Z", errorCode: "model_output_invalid" };
    const api = fakeApi({ startRun: vi.fn().mockResolvedValue(failed) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());

    expect(container.textContent).toContain("本次公开信息更新未完成");
    expect(container.textContent).toContain("继续显示最近一次有效报告");
    expect(container.textContent).toContain("最近一次有效报告");
    expect(container.textContent).not.toContain("model_output_invalid");
  });

  it("polls a queued run to completion and discovers the report produced by that exact run", async () => {
    vi.useFakeTimers();
    const queued = { ...runBase, state: "queued" as const, rowVersion: 1, startedAt: null };
    const completed = { ...runBase, state: "completed" as const, finishedAt: "2026-09-05T10:02:00Z", rowVersion: 3 };
    const generated = { ...insight, runId: completed.runId, insightVersionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", summary: "本次更新报告" };
    const generatedReport = { ...report, insight: generated };
    const api = fakeApi({
      startRun: vi.fn().mockResolvedValue(queued), runStatus: vi.fn().mockImplementation(async (runId: string) => runId === queued.runId ? completed : completedRun(runId)),
      listReports: vi.fn().mockResolvedValueOnce([insight]).mockResolvedValueOnce([generated, insight]),
      report: vi.fn().mockResolvedValueOnce(report).mockResolvedValueOnce(generatedReport),
    });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());

    await act(async () => vi.advanceTimersByTimeAsync(1500));

    expect(api.runStatus).toHaveBeenCalledWith(queued.runId, expect.any(AbortSignal));
    expect(api.report).toHaveBeenLastCalledWith(generated.insightVersionId, expect.any(AbortSignal));
    expect(container.textContent).toContain("本次更新报告");
    expect(container.textContent).not.toContain("model_output_invalid");
  });

  it("recovers from a temporary progress read failure with bounded backoff without unlocking a duplicate start", async () => {
    vi.useFakeTimers();
    const queued = { ...runBase, state: "queued" as const, rowVersion: 1, startedAt: null };
    const completed = { ...runBase, state: "completed" as const, finishedAt: "2026-09-05T10:02:00Z", rowVersion: 3 };
    const generated = { ...insight, runId: completed.runId, insightVersionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", summary: "恢复后的报告" };
    let progressReads = 0;
    const api = fakeApi({
      startRun: vi.fn().mockResolvedValue(queued),
      runStatus: vi.fn().mockImplementation(async (runId: string) => {
        if (runId !== queued.runId) return completedRun(runId);
        progressReads += 1;
        if (progressReads === 1) throw new Error("temporary");
        return completed;
      }),
      listReports: vi.fn().mockResolvedValueOnce([insight]).mockResolvedValueOnce([generated, insight]),
      report: vi.fn().mockResolvedValueOnce(report).mockResolvedValueOnce({ ...report, insight: generated }),
    });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());
    await act(async () => vi.advanceTimersByTimeAsync(1500));

    expect(container.textContent).toContain("更新仍在后台进行");
    expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.disabled).toBe(true);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(progressReads).toBe(3); // failed poll, recovered poll, then generated-report provenance
    expect(container.textContent).toContain("恢复后的报告");
  });

  it("resumes a retained active run after leaving and returning to Panorama", async () => {
    vi.useFakeTimers();
    const queued = { ...runBase, state: "queued" as const, rowVersion: 1, startedAt: null };
    const completed = { ...runBase, state: "completed" as const, finishedAt: "2026-09-05T10:02:00Z", rowVersion: 3 };
    const api = fakeApi({ startRun: vi.fn().mockResolvedValue(queued), runStatus: vi.fn().mockImplementation(async (runId: string) => runId === queued.runId ? completed : completedRun(runId)) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());
    expect(localStorage.getItem("platform.hr.panorama.active-run.member")).toContain(queued.runId);

    await act(async () => root.render(<div>对话</div>));
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(api.runStatus).toHaveBeenCalledWith(queued.runId, expect.any(AbortSignal));
    expect(api.startRun).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("platform.hr.panorama.active-run.member")).toBeNull();
  });

  it("replays an unresolved start with the same idempotency key and scope after navigation", async () => {
    vi.useFakeTimers();
    const queued = { ...runBase, state: "queued" as const, rowVersion: 1, startedAt: null };
    const firstStart = new Promise<HrPanoramaRun>(() => undefined);
    const startRun = vi.fn().mockReturnValueOnce(firstStart).mockResolvedValueOnce(queued);
    const api = fakeApi({ startRun });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    const update = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")!;
    await act(async () => { update.click(); await vi.advanceTimersByTimeAsync(0); });
    const [firstInput, firstKey] = startRun.mock.calls[0];
    expect(localStorage.getItem("platform.hr.panorama.active-run.member")).toContain(String(firstKey));

    await act(async () => root.render(<div>对话</div>));
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(startRun).toHaveBeenCalledTimes(2);
    expect(startRun.mock.calls[1][0]).toEqual(firstInput);
    expect(startRun.mock.calls[1][1]).toBe(firstKey);
    expect(localStorage.getItem("platform.hr.panorama.active-run.member")).toContain(queued.runId);
  });

  it("preserves but does not replay an unresolved start while mutations are read-only", async () => {
    const requestId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const input = { sourceIds: [source.sourceId, source2.sourceId] };
    localStorage.setItem("platform.hr.panorama.active-run.member", JSON.stringify({ kind: "starting", requestId, input }));
    const api = fakeApi();
    await act(async () => root.render(<HrPanoramaWorkspace account={{ ...account, hard_stale_read_only: true }} api={api} />));
    await act(async () => undefined);

    expect(api.startRun).not.toHaveBeenCalled();
    expect(localStorage.getItem("platform.hr.panorama.active-run.member")).toContain(requestId);
    expect(container.textContent).toContain("更新请求已安全保留");

    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    expect(api.startRun).toHaveBeenCalledWith(input, requestId, expect.any(AbortSignal));
  });

  it("pauses a stale 503 start without retrying or discarding its replay identity", async () => {
    vi.useFakeTimers();
    const api = fakeApi({ startRun: vi.fn().mockRejectedValue(new HrPanoramaApiError(503)) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());
    const retained = localStorage.getItem("platform.hr.panorama.active-run.member");
    await act(async () => vi.advanceTimersByTimeAsync(60_000));

    expect(api.startRun).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("platform.hr.panorama.active-run.member")).toBe(retained);
    expect(container.textContent).toContain("更新请求已安全保留");
  });

  it.each([403, 404])("unlocks a retained run after a permanent %s progress response", async (status) => {
    vi.useFakeTimers();
    const queued = { ...runBase, state: "queued" as const, rowVersion: 1, startedAt: null };
    const api = fakeApi({ startRun: vi.fn().mockResolvedValue(queued), runStatus: vi.fn().mockImplementation(async (runId: string) => {
      if (runId !== queued.runId) return completedRun(runId);
      throw new HrPanoramaApiError(status);
    }) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => { [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click(); await vi.advanceTimersByTimeAsync(0); });
    await act(async () => vi.advanceTimersByTimeAsync(1500));

    expect(localStorage.getItem("platform.hr.panorama.active-run.member")).toBeNull();
    expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.disabled).toBe(false);
    expect(container.textContent).toContain("你可以重新发起更新");
    expect(container.textContent).not.toContain(String(status));
  });

  it("keeps the last valid report visible when a completed version cannot yet be read", async () => {
    const completed = { ...runBase, state: "completed" as const, finishedAt: "2026-09-05T10:02:00Z", rowVersion: 3 };
    const api = fakeApi({ startRun: vi.fn().mockResolvedValue(completed) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.click());
    await act(async () => undefined);

    expect(container.textContent).toContain("新分析暂时无法读取");
    expect(container.textContent).toContain("最近一次有效报告");
  });

  it("starts a first update through the server-owned dedicated execution conversation", async () => {
    const api = fakeApi({ listReports: vi.fn().mockResolvedValue([]) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    const update = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新");
    expect(update?.disabled).toBe(false);
    await act(async () => update?.click());
    expect(api.startRun).toHaveBeenCalledWith({ sourceIds: [source.sourceId, source2.sourceId] }, expect.any(String), expect.any(AbortSignal));
  });

  it("prevents a rapid second click from creating a duplicate run", async () => {
    let resolveStart: ((value: HrPanoramaRun) => void) | undefined;
    const api = fakeApi({ startRun: vi.fn().mockImplementation(() => new Promise<HrPanoramaRun>((resolve) => { resolveStart = resolve; })) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    const update = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")!;

    act(() => { update.click(); update.click(); });

    expect(api.startRun).toHaveBeenCalledTimes(1);
    expect(update.disabled).toBe(true);
    expect(container.textContent).toContain("正在收集公开招聘岗位");
    await act(async () => resolveStart?.(runBase));
  });

  it("prevents a rapid second click from adding the same company twice", async () => {
    let resolveAdd: ((value: HrPanoramaSource) => void) | undefined;
    const api = fakeApi({ addCompany: vi.fn().mockImplementation(() => new Promise<HrPanoramaSource>((resolve) => { resolveAdd = resolve; })) });
    await act(async () => root.render(<HrPanoramaWorkspace account={account} api={api} />));
    await act(async () => undefined);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "添加关注公司")?.click());
    const inputs = container.querySelectorAll<HTMLInputElement>(".hr-panorama-add-company input");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(inputs[0], "新公司");
      inputs[0].dispatchEvent(new Event("input", { bubbles: true }));
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(inputs[1], "https://new.example/jobs");
      inputs[1].dispatchEvent(new Event("input", { bubbles: true }));
    });
    const confirm = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "确认关注")!;

    act(() => { confirm.click(); confirm.click(); });

    expect(api.addCompany).toHaveBeenCalledTimes(1);
    expect(confirm.disabled).toBe(true);
    await act(async () => resolveAdd?.(source));
  });

  it("does not offer mutations while directory access is read-only", async () => {
    const api = fakeApi();
    await act(async () => root.render(<HrPanoramaWorkspace account={{ ...account, hard_stale_read_only: true }} api={api} />));
    await act(async () => undefined);

    expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "添加关注公司")?.disabled).toBe(true);
    expect([...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "立即更新")?.disabled).toBe(true);
  });
});
