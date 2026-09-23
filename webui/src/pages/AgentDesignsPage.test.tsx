/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentDesignsPage } from "./AgentDesignsPage";
const source = { repository: "synthetic", path: "design.md", commit: "a".repeat(40), sha256: "b".repeat(64), captured_at: "2026-09-23", working_tree_modified: false };
const hr = { slug: "hr", agent: "HR Agent", title: "总体架构设计", source };
const fae = { ...hr, slug: "fae", agent: "FAE Agent" };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
let root: ReturnType<typeof createRoot>; let box: HTMLDivElement;
beforeEach(() => { box = document.createElement("div"); document.body.append(box); root = createRoot(box); (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); box.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it("opens the first design and preserves explicit anchors without rendering executable HTML", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ documents: [hr] }))
    .mockResolvedValueOnce(json({ ...hr, markdown: '# HR 设计\n\n<a id="overview"></a>\n\n## 架构总览\n\n目标架构，未全部实施。\n<script>alert(1)</script>' })));
  await act(async () => root.render(<AgentDesignsPage />));
  expect(box.querySelector("h2#overview")?.textContent).toBe("架构总览");
  expect(box.querySelector("nav a[href='#overview']")?.textContent).toBe("架构总览");
  expect(box.querySelector("script")).toBeNull();
  expect(box.textContent).toContain("目标架构，未全部实施。");
  const heading = box.querySelector("h2#overview")!;
  const scroll = vi.fn(); Object.defineProperty(heading, "scrollIntoView", { value: scroll });
  await act(async () => (box.querySelector("nav a[href='#overview']") as HTMLAnchorElement).click());
  expect(scroll).toHaveBeenCalledOnce();
});
it("switches documents in place and clears protected content on access loss", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ documents: [hr, fae] }))
    .mockResolvedValueOnce(json({ ...hr, markdown: '# HR 私有内容' })).mockResolvedValueOnce(json({}, 403));
  vi.stubGlobal("fetch", fetch); await act(async () => root.render(<AgentDesignsPage />));
  const url = window.location.href;
  const select = box.querySelector("select")!;
  await act(async () => { select.value = "fae"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(window.location.href).toBe(url);
  expect(box.textContent).not.toContain("HR 私有内容"); expect(box.querySelector("nav")).toBeNull();
  expect(box.textContent).toContain("无权限，请联系苍渊。");
});
it("does not display a stale document when selection changes during a read", async () => {
  let done!: (response: Response) => void;
  const fetch = vi.fn().mockResolvedValueOnce(json({ documents: [hr, fae] }))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { done = resolve; }))
    .mockResolvedValueOnce(json({ ...fae, markdown: '# FAE 当前文档' }));
  vi.stubGlobal("fetch", fetch); await act(async () => root.render(<AgentDesignsPage />));
  const select = box.querySelector("select")!;
  await act(async () => { select.value = "fae"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => done(json({ ...hr, markdown: '# HR 迟到文档' })));
  expect(box.textContent).toContain("FAE 当前文档"); expect(box.textContent).not.toContain("HR 迟到文档");
});
it("retries a failed index read", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({}, 503)).mockResolvedValueOnce(json({ documents: [] })));
  await act(async () => root.render(<AgentDesignsPage />));
  await act(async () => box.querySelector("button")!.click());
  expect(box.textContent).toContain("暂无设计文档");
});
