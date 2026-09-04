/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessHistoryPage } from "./AccessHistoryPage";
import type { AccessHistoryPageResult, AccessSubjectPageResult } from "../accessHistoryApi";

const subjects: AccessSubjectPageResult = {
  items: [{
    display_name: "苍渊", departments: ["总经办"], event_count: 12,
    latest_occurred_at: "2026-09-04T02:03:04Z", latest_event_kind: "page_view",
    latest_workspace_key: "office", latest_module_display_name: "行政服务",
    latest_page_display_name: "行政服务门户", latest_agent_id: null,
  }], limit: 20, offset: 0, has_more: false,
};

const events: AccessHistoryPageResult = {
  items: [
    { access_event_id: "1", display_name: "苍渊", departments: ["总经办"], event_kind: "login_succeeded", login_kind: "qr", workspace_key: null, page_key: null, module_display_name: null, page_display_name: null, agent_id: null, occurred_at: "2026-09-04T01:02:03Z" },
    { access_event_id: "2", display_name: "苍渊", departments: ["总经办"], event_kind: "page_view", login_kind: null, workspace_key: "office", page_key: "office.services", module_display_name: "行政服务", page_display_name: "行政服务门户", agent_id: null, occurred_at: "2026-09-04T02:03:04Z" },
  ], limit: 100, offset: 0, has_more: false,
};

describe("AccessHistoryPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("indexes people by nickname and current department before loading details", async () => {
    const loadSubjects = vi.fn().mockResolvedValue(subjects);
    const loadEvents = vi.fn().mockResolvedValue(events);
    await act(async () => root.render(<AccessHistoryPage loadAccessSubjects={loadSubjects} loadAccessEvents={loadEvents} />));
    expect(container.textContent).toContain("苍渊");
    expect(container.textContent).toContain("总经办");
    expect(container.textContent).toContain("12次访问");
    expect(container.textContent).toContain("行政服务");
    expect(container.textContent).toContain("行政服务门户");
    expect(loadEvents).not.toHaveBeenCalled();

    const expand = container.querySelector<HTMLButtonElement>('button[aria-expanded="false"]');
    await act(async () => expand?.click());
    expect(loadEvents).toHaveBeenCalledWith(expect.objectContaining({ display_name: "苍渊", limit: 100, offset: 0 }), expect.any(AbortSignal));
    expect(container.textContent).toContain("钉钉扫码登录");
    expect(container.textContent).toContain("Agent Platform");
    expect(container.textContent).not.toContain("session_id");
  });

  it("distinguishes index and expanded timeline failures", async () => {
    const loadSubjects = vi.fn().mockResolvedValue(subjects);
    const loadEvents = vi.fn().mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<AccessHistoryPage loadAccessSubjects={loadSubjects} loadAccessEvents={loadEvents} />));
    await act(async () => container.querySelector<HTMLButtonElement>('button[aria-expanded="false"]')?.click());
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("该员工的访问明细暂不可用");
  });

  it("loads older events inside the expanded nickname without paging the people index", async () => {
    const loadSubjects = vi.fn().mockResolvedValue(subjects);
    const loadEvents = vi.fn()
      .mockResolvedValueOnce({ ...events, has_more: true })
      .mockResolvedValueOnce({ ...events, items: [], offset: 100, has_more: false });
    await act(async () => root.render(<AccessHistoryPage loadAccessSubjects={loadSubjects} loadAccessEvents={loadEvents} />));
    await act(async () => container.querySelector<HTMLButtonElement>('button[aria-expanded="false"]')?.click());
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "加载更早记录")?.click());
    expect(loadEvents).toHaveBeenLastCalledWith(expect.objectContaining({ display_name: "苍渊", offset: 100 }), expect.any(AbortSignal));
    expect(loadSubjects).toHaveBeenCalledTimes(1);
  });

  it("distinguishes unavailable people from an empty index", async () => {
    const loadSubjects = vi.fn().mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<AccessHistoryPage loadAccessSubjects={loadSubjects} />));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("访问记录暂不可用");
    expect(container.textContent).not.toContain("暂无访问记录");
  });
});
