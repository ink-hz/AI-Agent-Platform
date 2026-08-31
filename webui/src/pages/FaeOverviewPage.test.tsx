/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { faeWorkbenchApi } from "../faeWorkbenchApi";
import type { FaeOverview, FaeSectionState } from "../faeWorkbenchTypes";
import { FaeOverviewPage } from "./FaeOverviewPage";


const availableState: FaeSectionState = {
  status: "available",
  as_of: "2026-08-31T17:00:00+08:00",
  error_code: null,
};

const unavailableState = (errorCode: string): FaeSectionState => ({
  status: "unavailable",
  as_of: null,
  error_code: errorCode,
});

const freshOverview: FaeOverview = {
  period_start: "2026-08-24T00:00:00+08:00",
  period_end: "2026-08-31T00:00:00+08:00",
  timezone: "Asia/Shanghai",
  freshness: { status: "fresh", data_as_of: "2026-08-31T17:00:00+08:00" },
  summary: {
    state: availableState,
    data: {
      session_count: 12,
      active_subject_count: 8,
      negative_feedback_events: 3,
      negative_turn_count: 2,
      abnormal_session_count: 4,
      open_issue_count: 5,
      p50_duration_ms: 180,
      p95_duration_ms: 640,
    },
  },
  attention: {
    state: availableState,
    items: [
      {
        session_key: "fae:session-1",
        title: "安装失败排查",
        last_active_at: "2026-08-31T16:30:00+08:00",
        reason: "failed_outcome",
      },
      {
        session_key: "fae:session-2",
        title: null,
        last_active_at: "2026-08-31T15:00:00+08:00",
        reason: "fallback",
      },
    ],
  },
  trends: {
    state: availableState,
    points: [
      { day: "2026-08-24", sessions: 4, negative_turns: 1 },
      { day: "2026-08-25", sessions: 7, negative_turns: 0 },
      { day: "2026-08-26", sessions: 5, negative_turns: 2 },
      { day: "2026-08-27", sessions: 9, negative_turns: 1 },
      { day: "2026-08-28", sessions: 12, negative_turns: 3 },
      { day: "2026-08-29", sessions: 8, negative_turns: 2 },
      { day: "2026-08-30", sessions: 10, negative_turns: 1 },
    ],
  },
  issues: {
    state: availableState,
    statuses: { pending_triage: 2, fixing: 2, awaiting_review: 1, closed: 7, duplicate: 1 },
  },
  reports: { state: unavailableState("reports_not_integrated") },
};

const periodSessionsHref = "/admin/fae/sessions?date_from=2026-08-24T00%3A00%3A00%2B08%3A00&date_to=2026-08-31T00%3A00%3A00%2B08%3A00";
const negativeSessionsHref = "/admin/fae/sessions?sentiment=negative&date_from=2026-08-24T00%3A00%3A00%2B08%3A00&date_to=2026-08-31T00%3A00%3A00%2B08%3A00";


describe("FaeOverviewPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    window.history.replaceState({}, "", "/admin/fae");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.querySelector('meta[name="platform-identity-mode"]')?.remove();
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function renderOverview(value: FaeOverview) {
    vi.spyOn(faeWorkbenchApi, "overview").mockResolvedValue(value);
    await act(async () => root.render(<FaeOverviewPage />));
  }

  it("renders the approved freshness-first order, drill-down cards, queues, trend and report preview", async () => {
    await renderOverview(freshOverview);

    const text = container.querySelector(".fae-overview")?.textContent || "";
    expect(text).toContain("统计周期 08月24日 00:00 至 08月31日 00:00");
    expect(text).toContain("数据截止 08月31日 17:00");
    expect(text).toContain("12 个 Session");
    expect(text).toContain("8 个活跃主体");
    expect(text).toContain("2 个负向 Turn");
    expect(text).toContain("4 个异常 Session");
    expect(text).toContain("5 个开放 Issue");
    expect(text).toContain("p95 640 ms");
    expect(text.indexOf("数据截止")).toBeLessThan(text.indexOf("12 个 Session"));
    expect(text.indexOf("12 个 Session")).toBeLessThan(text.indexOf("问题治理"));
    expect(text.indexOf("问题治理")).toBeLessThan(text.indexOf("7 日趋势"));
    expect(text.indexOf("7 日趋势")).toBeLessThan(text.indexOf("分析报告"));
    expect(text).not.toContain("实时");

    expect(container.querySelector('[data-metric="sessions"] a')?.getAttribute("href")).toBe(periodSessionsHref);
    expect(container.querySelector('[data-metric="negative-turns"] a')?.getAttribute("href")).toBe(negativeSessionsHref);
    expect(container.querySelector('[data-metric="p95-latency"] a')?.getAttribute("href")).toBe(periodSessionsHref);
    expect(container.querySelector('[data-metric="active-subjects"] a')).toBeNull();
    expect(container.querySelector('[data-metric="active-subjects"]')?.textContent).toContain("暂无主体维度下钻");
    expect(container.querySelector('[data-metric="abnormal-sessions"] a')).toBeNull();
    expect(container.querySelector('[data-metric="abnormal-sessions"]')?.textContent).toContain("请从下方异常 Session 打开详情");
    expect(container.querySelector('a[href*="outcome=failed"]')).toBeNull();
    expect(container.querySelector('[data-metric="open-issues"] a[href="/admin/fae/issues?status=open"]')).not.toBeNull();
    expect(container.querySelector('.fae-overview-list a[href="/admin/fae/issues?status=pending_triage"]')).not.toBeNull();
    expect(container.querySelector('.fae-overview-list a[href="/admin/fae/issues?status=fixing"]')).not.toBeNull();
    expect(container.querySelector('.fae-overview-list a[href="/admin/fae/issues?status=awaiting_review"]')).not.toBeNull();
    expect(container.querySelector('a[href="/admin/fae/sessions/fae%3Asession-1"]')).not.toBeNull();
    expect(container.querySelector('.fae-report-preview > a[href="/admin/fae/reports"]')?.textContent).toContain("查看接入状态");

    const bars = [...container.querySelectorAll<HTMLElement>(".fae-trend-bar")];
    expect(bars).toHaveLength(14);
    expect(bars.every((bar) => Boolean(bar.getAttribute("aria-label")))).toBe(true);
    expect(container.querySelectorAll(".fae-trend-bar__value")).toHaveLength(14);
    expect(container.textContent).toContain("分析报告尚未接入");
    expect(container.textContent).not.toContain("示例报告");
  });

  it("renders nullable metrics as unavailable instead of zero", async () => {
    await renderOverview({
      ...freshOverview,
      summary: {
        ...freshOverview.summary,
        data: { ...freshOverview.summary.data!, open_issue_count: null, p95_duration_ms: null },
      },
    });

    expect(container.querySelector('[data-metric="open-issues"]')?.textContent).toContain("暂不可用");
    expect(container.querySelector('[data-metric="p95-latency"]')?.textContent).toContain("暂不可用");
    expect(container.querySelector('[data-metric="open-issues"]')?.textContent).not.toContain("0");
    expect(container.querySelector('[data-metric="p95-latency"]')?.textContent).not.toContain("0");
    expect(container.querySelector('[data-metric="open-issues"] a')).toBeNull();
    expect(container.querySelector('[data-metric="p95-latency"] a')).toBeNull();
  });

  it("keeps available issue and report sections when the operational summary is unavailable", async () => {
    await renderOverview({
      ...freshOverview,
      freshness: { status: "unavailable", data_as_of: null },
      summary: { state: unavailableState("summary_unavailable"), data: null },
      attention: { state: unavailableState("attention_unavailable"), items: [] },
      trends: { state: unavailableState("trends_unavailable"), points: [] },
    });

    expect(container.textContent).toContain("数据截止 暂不可用");
    expect(container.textContent).toContain("运营摘要暂不可用");
    expect(container.textContent).toContain("问题治理");
    expect(container.textContent).toContain("待归因 2");
    expect(container.textContent).toContain("异常 Session 暂不可用");
    expect(container.textContent).toContain("7 日趋势暂不可用");
    expect(container.textContent).toContain("分析报告尚未接入");
    expect(container.textContent).not.toContain("实时");
  });

  it("marks hard-stale data truthfully while preserving independently available content", async () => {
    await renderOverview({
      ...freshOverview,
      freshness: { status: "stale", data_as_of: "2026-08-29T03:15:00Z" },
      issues: { state: unavailableState("issues_unavailable"), statuses: {} },
    });

    expect(container.querySelector(".fae-overview__freshness")?.textContent).toContain("数据已过期");
    expect(container.textContent).toContain("数据截止 08月29日 11:15");
    expect(container.textContent).toContain("12 个 Session");
    expect(container.textContent).toContain("问题治理暂不可用");
    expect(container.textContent).toContain("安装失败排查");
    expect(container.textContent).toContain("7 日趋势");
    expect(container.textContent).not.toContain("实时");
  });

  it("preserves the preview prefix on all platform links", async () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/admin/fae");
    await renderOverview(freshOverview);

    const links = [...container.querySelectorAll<HTMLAnchorElement>(".fae-overview a")];
    expect(links.length).toBeGreaterThan(0);
    expect(links.every((link) => link.getAttribute("href")?.startsWith("/_preview/dingtalk-r1/admin/fae"))).toBe(true);
    expect(container.querySelector('[data-metric="negative-turns"] a')?.getAttribute("href")).toBe(`/_preview/dingtalk-r1${negativeSessionsHref}`);
    expect(container.querySelector('.fae-overview-list a[href="/_preview/dingtalk-r1/admin/fae/issues?status=pending_triage"]')).not.toBeNull();
  });
});


describe("FAE overview routing and authorization", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    const identityMeta = document.createElement("meta");
    identityMeta.name = "platform-identity-mode";
    identityMeta.content = "enabled";
    document.head.append(identityMeta);
    window.history.replaceState({}, "", "/admin/fae");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.querySelector('meta[name="platform-identity-mode"]')?.remove();
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const account = (role: "platform_owner" | "management_viewer") => ({
    internal_user_id: role,
    display_name: role === "platform_owner" ? "负责人" : "观察者",
    role,
    departments: [],
    gender: null,
    observation_agent_ids: ["ai-fae-agent"],
    directory_freshness: "fresh",
    hard_stale_read_only: false,
    csrf_token: "csrf",
  });

  it("maps the overview route to the production overview page", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith("/api/v1/account")) return new Response(JSON.stringify(account("platform_owner")), {
        headers: { "Content-Type": "application/json" },
      });
      if (path.endsWith("/api/admin/fae/overview")) return new Response(JSON.stringify(freshOverview));
      if (path.endsWith("/api/deployment")) return new Response(JSON.stringify({
        mode: "local", read_only: false, auth: "dingtalk", freshness: "current", last_success_at: null,
      }));
      return new Response("{}", { status: 404 });
    }));

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.textContent).toContain("12 个 Session");
    expect(container.textContent).not.toContain("该工作区正在接入真实 FAE 运营数据");
  });

  it("does not grant a management viewer any FAE route through frontend route admission", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith("/api/v1/account")) return new Response(JSON.stringify(account("management_viewer")), {
        headers: { "Content-Type": "application/json" },
      });
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<App />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.textContent).toContain("无权访问");
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/admin/fae/overview"))).toBe(false);
  });
});
