/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ManagementMutationIndeterminate, type Account } from "../auth";
import type { VocAccessGrant } from "../vocAccessApi";
import { VocAccessPanel } from "./VocAccessPanel";


const owner: Account = {
  internal_user_id: "10000000-0000-4000-8000-000000000001",
  display_name: "苍渊",
  role: "platform_owner",
  departments: [],
  gender: null,
  observation_agent_ids: [],
  workspace_scopes: [],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf",
};

const grant: VocAccessGrant = {
  grant_id: "20000000-0000-4000-8000-000000000001",
  internal_user_id: "30000000-0000-4000-8000-000000000001",
  display_name: "稻夫",
  status: "active",
  permission: "manager",
  created_at: "2026-09-04T01:02:03Z",
  row_version: 7,
};


async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}


describe("VocAccessPanel", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("is owner-only and renders the bounded VOC grant projection", async () => {
    const api = {
      list: vi.fn().mockResolvedValue([grant]),
      grant: vi.fn(),
      revoke: vi.fn(),
    };
    await act(async () => root.render(<VocAccessPanel account={owner} api={api} />));
    await settle();
    expect(container.textContent).toContain("VOC 工作台访问");
    expect(container.textContent).toContain("稻夫");
    expect(container.textContent).toContain("行版本 7");
    expect(container.textContent).not.toContain(grant.internal_user_id);

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(
      <VocAccessPanel account={{ ...owner, role: "platform_admin" }} api={api} />,
    ));
    await settle();
    expect(container.textContent).toBe("");
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it("grants by flower name and reuses the same indeterminate request", async () => {
    const requestId = "50000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const api = {
      list: vi.fn().mockResolvedValue([]),
      grant: vi.fn()
        .mockRejectedValueOnce(new ManagementMutationIndeterminate(requestId, {}))
        .mockResolvedValueOnce(undefined),
      revoke: vi.fn(),
    };
    await act(async () => root.render(<VocAccessPanel account={owner} api={api} />));
    await settle();
    const input = container.querySelector("input[aria-label='VOC 授权花名']") as HTMLInputElement;
    await act(async () => {
      input.value = "稻夫";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const grantButton = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "授予 VOC 访问",
    ) as HTMLButtonElement;
    await act(async () => grantButton.click());
    await settle();
    expect(container.textContent).toContain("结果暂时无法确认");
    const retry = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "使用同一请求重试",
    ) as HTMLButtonElement;
    await act(async () => retry.click());
    await settle();
    expect(api.grant).toHaveBeenNthCalledWith(1, owner, "稻夫", requestId);
    expect(api.grant).toHaveBeenNthCalledWith(2, owner, "稻夫", requestId);
    expect(globalThis.crypto.randomUUID).toHaveBeenCalledTimes(1);
  });

  it("revokes the selected VOC grant with its current row version", async () => {
    const requestId = "60000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const api = {
      list: vi.fn().mockResolvedValueOnce([grant]).mockResolvedValueOnce([]),
      grant: vi.fn(),
      revoke: vi.fn().mockResolvedValue(undefined),
    };
    await act(async () => root.render(<VocAccessPanel account={owner} api={api} />));
    await settle();
    const revoke = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "撤销 VOC 访问",
    ) as HTMLButtonElement;
    await act(async () => revoke.click());
    await settle();
    expect(api.revoke).toHaveBeenCalledWith(owner, grant, requestId);
    expect(container.textContent).toContain("撤销成功");
  });
});
