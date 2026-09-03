/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessHistoryPage } from "./AccessHistoryPage";
import type { AccessHistoryPageResult } from "../accessHistoryApi";

const page: AccessHistoryPageResult = {
  items: [
    { access_event_id: "1", display_name: "苍渊", event_kind: "login_succeeded", login_kind: "qr", workspace_key: null, page_key: null, page_display_name: null, agent_id: null, occurred_at: "2026-09-04T01:02:03Z" },
    { access_event_id: "2", display_name: "西门吹雪", event_kind: "page_view", login_kind: null, workspace_key: "office", page_key: "office.services", page_display_name: "行政服务门户", agent_id: null, occurred_at: "2026-09-04T02:03:04Z" },
  ], limit: 50, offset: 0, has_more: false,
};

describe("AccessHistoryPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("shows a concise owner audit table without technical identifiers", async () => {
    const load = vi.fn().mockResolvedValue(page);
    await act(async () => root.render(<AccessHistoryPage loadAccessEvents={load} />));
    expect(container.textContent).toContain("登录与页面访问");
    expect(container.textContent).toContain("苍渊");
    expect(container.textContent).toContain("钉钉扫码登录");
    expect(container.textContent).toContain("西门吹雪");
    expect(container.textContent).toContain("行政服务门户");
    expect(container.textContent).not.toContain("00000000");
    expect(container.textContent).not.toContain("session_id");
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ limit: 50, offset: 0 }), expect.any(AbortSignal));
  });

  it("distinguishes unavailable data from an empty history", async () => {
    const load = vi.fn().mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<AccessHistoryPage loadAccessEvents={load} />));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("访问记录暂不可用");
    expect(container.textContent).not.toContain("暂无访问记录");
  });
});
