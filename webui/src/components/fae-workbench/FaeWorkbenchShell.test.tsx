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
      <FaeWorkbenchShell currentSection="sessions"><p>Session detail</p></FaeWorkbenchShell>,
    ));

    const shell = container.querySelector(".fae-workbench");
    expect(shell?.firstElementChild?.classList.contains("fae-workbench__sidebar")).toBe(true);
    expect(shell?.lastElementChild?.classList.contains("fae-workbench__content")).toBe(true);
    expect([...container.querySelectorAll<HTMLAnchorElement>(".fae-workbench__sidebar a")].map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["概览", "/admin/fae"],
      ["Sessions", "/admin/fae/sessions"],
      ["问题治理", "/admin/fae/issues"],
      ["分析报告", "/admin/fae/reports"],
    ]);
    expect(container.querySelector<HTMLAnchorElement>('a[href="/admin/fae/sessions"]')?.getAttribute("aria-current")).toBe("page");
    expect(container.querySelector(".fae-workbench__content")?.textContent).toContain("Session detail");
  });
});
