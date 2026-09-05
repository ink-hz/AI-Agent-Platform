/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Account } from "../../auth";
import { HrWorkspaceShell } from "./HrWorkspaceShell";


const account: Account = {
  internal_user_id: "member", display_name: "磐德", role: "member",
  departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [],
  directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
};


describe("HrWorkspaceShell", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("owns HR product identity and exposes only working primary destinations", async () => {
    await act(async () => root.render(<HrWorkspaceShell account={account} current="chat"><p>对话内容</p></HrWorkspaceShell>));

    expect(container.querySelector(".hr-workspace-shell")).not.toBeNull();
    expect(container.querySelector(".hr-workspace-brand")?.textContent).toContain("HR 智能工作台");
    expect(container.querySelector<HTMLAnchorElement>('.hr-workspace-nav a[href="/hr/"]')?.getAttribute("aria-current")).toBe("page");
    expect(container.querySelector<HTMLAnchorElement>('.hr-workspace-nav a[href="/hr/positions"]')?.textContent).toBe("岗位");
    expect(container.querySelector<HTMLAnchorElement>('.hr-workspace-nav a[href="/hr/panorama"]')?.textContent).toBe("全景分析");
    expect(container.querySelector(".hr-workspace-identity")?.textContent).toContain("磐德");
    expect(container.querySelector<HTMLAnchorElement>('.hr-workspace-platform-link')?.getAttribute("href")).toBe("/");
    expect(container.querySelectorAll<HTMLAnchorElement>(".hr-workspace-topbar a")).toHaveLength(5);
    expect(container.textContent).not.toContain("专业 Agent");
  });

  it("marks panorama as an independent first-level destination", async () => {
    await act(async () => root.render(<HrWorkspaceShell account={account} current="panorama"><p>报告</p></HrWorkspaceShell>));

    expect(container.querySelector<HTMLAnchorElement>('.hr-workspace-nav a[href="/hr/panorama"]')
      ?.getAttribute("aria-current")).toBe("page");
    expect(container.querySelector('.hr-workspace-nav a[aria-current="page"]')?.textContent).toBe("全景分析");
  });

  it("shows the directory stale state inside the independent HR product", async () => {
    await act(async () => root.render(<HrWorkspaceShell account={{ ...account, hard_stale_read_only: true }} current="positions"><p>岗位内容</p></HrWorkspaceShell>));

    expect(container.querySelector('.hr-workspace-nav a[aria-current="page"]')?.textContent).toBe("岗位");
    expect(container.querySelector(".hr-workspace-stale")?.textContent).toContain("只读");
  });

  it("returns to the last active HR conversation from the positions view", async () => {
    await act(async () => root.render(<HrWorkspaceShell
      account={account}
      chatHref="/hr/conversations/c-9"
      current="positions"
    ><p>岗位内容</p></HrWorkspaceShell>));

    expect(container.querySelector<HTMLAnchorElement>(
      '.hr-workspace-nav a[href="/hr/conversations/c-9"]',
    )?.textContent).toBe("对话");
  });
});
