/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MissionsPage } from "./MissionsPage";

const saved = { conversation_id: "synthetic-one", mode: "brain", direct_agent_id: null, title: "合成历史工作",
  status: "active", summary_through_seq: 0, created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-21T00:00:00Z", archived_at: null };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
let box: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { box = document.createElement("div"); document.body.append(box); root = createRoot(box);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); box.remove(); vi.unstubAllGlobals(); });
async function click(label: string) {
  const button = [...box.querySelectorAll("button")].find(item => item.textContent === label);
  expect(button).toBeDefined(); await act(async () => button!.click());
}
it("reads current conversation history and opens saved threads without requesting legacy Missions", async () => {
  const fetch = vi.fn().mockResolvedValue(json({ items: [saved, { ...saved, conversation_id: "synthetic-direct", mode: "direct_agent", direct_agent_id: "hr-bot", title: "合成专业工作", activity_status: "waiting_user", unread: false }], next_cursor: null }));
  vi.stubGlobal("fetch", fetch); await act(async () => root.render(<MissionsPage />));
  expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/v1/conversations?limit=20"]);
  expect(box.querySelector("a[href='/conversations/synthetic-one']")?.textContent).toContain("合成历史工作");
  expect(box.querySelector("a[href='/conversations/synthetic-direct']")?.textContent).toContain("需要补充");
  expect(box.textContent).not.toContain("YOUR WORK");
  expect(box.querySelector("a[href='/conversations/synthetic-one'] b")).toBeNull();
});
it("keeps loaded rows on pagination failure and retries the same cursor", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ items: [saved], next_cursor: "cursor:1" }))
    .mockResolvedValueOnce(json({ detail: "unavailable" }, 503))
    .mockResolvedValueOnce(json({ items: [saved, { ...saved, conversation_id: "synthetic-two", title: "更早合成工作" }], next_cursor: null }));
  vi.stubGlobal("fetch", fetch); await act(async () => root.render(<MissionsPage />));
  await click("加载更早任务"); expect(box.textContent).toContain("合成历史工作"); expect(box.querySelector("[role='alert']")).not.toBeNull();
  await click("重试加载"); expect(box.querySelectorAll(".mission-history-list a")).toHaveLength(2);
  expect(fetch.mock.calls[1][0]).toBe("/api/v1/conversations?limit=20&before=cursor%3A1");
  expect(fetch.mock.calls[2][0]).toBe(fetch.mock.calls[1][0]);
  expect(box.querySelector("[role='alert']")).toBeNull();
});
it("loads archived history with its own pagination", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ items: [saved], next_cursor: "active-cursor" }))
    .mockResolvedValueOnce(json({ items: [{ ...saved, status: "archived", archived_at: "2026-09-21T00:00:00Z" }], next_cursor: "archive-cursor" }))
    .mockResolvedValueOnce(json({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetch); await act(async () => root.render(<MissionsPage />));
  await click("已归档"); await click("加载更早任务");
  expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/v1/conversations?limit=20", "/api/v1/conversations?limit=20&status=archived", "/api/v1/conversations?limit=20&before=archive-cursor&status=archived"]);
});
it("ignores a late response after switching history scope", async () => {
  let done!: (response: Response) => void;
  const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { done = resolve; }))
    .mockResolvedValueOnce(json({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetch); await act(async () => root.render(<MissionsPage />));
  const signal = fetch.mock.calls[0][1].signal as AbortSignal;
  await click("已归档"); await act(async () => done(json({ items: [saved], next_cursor: null })));
  expect(signal.aborted).toBe(true); expect(box.textContent).not.toContain("合成历史工作"); expect(box.textContent).toContain("暂无归档任务");
});
it.each([401, 403])("shows an explicit access error for %s without claiming service health", async status => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: "denied" }, status)));
  await act(async () => root.render(<MissionsPage />));
  expect(box.textContent).toContain(status === 401 ? "请重新登录" : "无权读取历史任务");
  expect(box.textContent).not.toContain("Agent 服务不受影响");
  expect(box.textContent).not.toContain("还没有历史任务");
});
it("retries a failed initial read and distinguishes an empty result", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({}, 503)).mockResolvedValueOnce(json({ items: [], next_cursor: null })));
  await act(async () => root.render(<MissionsPage />)); expect(box.textContent).toContain("历史任务暂时无法读取");
  await click("重试"); expect(box.textContent).toContain("还没有历史任务"); expect(box.querySelector("[role='alert']")).toBeNull();
});
it("clears already displayed history if pagination loses authorization", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ items: [saved], next_cursor: "private-cursor" }))
    .mockResolvedValueOnce(json({ detail: "denied" }, 403)));
  await act(async () => root.render(<MissionsPage />));
  await click("加载更早任务");
  expect(box.textContent).toContain("无权读取历史任务");
  expect(box.textContent).not.toContain("合成历史工作");
  expect(box.querySelector(".mission-load-more")).toBeNull();
});
