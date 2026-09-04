/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { FaeWorkbenchShell } from "./FaeWorkbenchShell";


describe("FAE workbench shell", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("renders the rail before content with all workbench views and the selected detail section", async () => {
    await act(async () => root.render(
      <main><FaeWorkbenchShell currentSection="sessions"><p>Session detail</p></FaeWorkbenchShell></main>,
    ));

    const shell = container.querySelector(".fae-workbench");
    expect(shell?.firstElementChild?.classList.contains("fae-workbench__sidebar")).toBe(true);
    expect(shell?.lastElementChild?.classList.contains("fae-workbench__content")).toBe(true);
    expect([...container.querySelectorAll<HTMLAnchorElement>('.fae-workbench__workspace-nav a')].map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["返回 FAE Agent", "/fae/"],
      ["管理", "/fae/manage/"],
    ]);
    expect([...container.querySelectorAll<HTMLAnchorElement>(".fae-workbench__sections a")].map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["概览", "/fae/manage/"],
      ["Sessions", "/fae/manage/sessions"],
      ["反馈与修复", "/fae/manage/issues"],
      ["分析报告", "/fae/manage/reports"],
    ]);
    expect(container.querySelector<HTMLAnchorElement>('.fae-workbench__workspace-nav a[href="/fae/"]')?.onclick).toBeNull();
    expect(container.querySelector<HTMLAnchorElement>('a[href="/fae/manage/sessions"]')?.getAttribute("aria-current")).toBe("page");
    expect(container.querySelector(".fae-workbench__content")?.textContent).toContain("Session detail");
    expect(container.querySelector(".fae-workbench__content")?.tagName).toBe("DIV");
    expect(container.querySelectorAll("main")).toHaveLength(1);
  });
});
