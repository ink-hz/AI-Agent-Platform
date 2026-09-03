/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ManagementMutationIndeterminate, type Account } from "../auth";
import type { FaeAccessGrant } from "../faeAccessApi";
import { FaeAccessPanel } from "./FaeAccessPanel";


const owner: Account = {
  internal_user_id: "10000000-0000-4000-8000-000000000001",
  display_name: "苍渊",
  role: "platform_owner",
  departments: [],
  gender: null,
  observation_agent_ids: [],
  workspace_scopes: ["fae_workbench"],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf",
};

const grant: FaeAccessGrant = {
  grant_id: "20000000-0000-4000-8000-000000000001",
  internal_user_id: "30000000-0000-4000-8000-000000000001",
  display_name: "天启",
  status: "active",
  permission: "manager",
  created_at: "2026-09-03T01:02:03Z",
  row_version: 7,
};


function inputValue(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
}


async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}


describe("FaeAccessPanel", () => {
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

  it("renders only for the owner and shows the bounded grant projection", async () => {
    const api = {
      list: vi.fn().mockResolvedValue([grant]),
      grant: vi.fn(),
      revoke: vi.fn(),
    };

    await act(async () => root.render(<FaeAccessPanel account={owner} api={api} />));
    await settle();

    expect(container.textContent).toContain("FAE 工作台访问");
    expect(container.textContent).toContain("天启");
    expect(container.textContent).toContain("在职");
    expect(container.textContent).toContain("行版本 7");
    expect(container.textContent).toContain("2026");
    expect(container.textContent).not.toContain(grant.internal_user_id);

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(
      <FaeAccessPanel account={{ ...owner, role: "platform_admin" }} api={api} />,
    ));
    await settle();
    expect(container.textContent).toBe("");
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it("grants by unique enterprise display name without accepting a target UUID", async () => {
    const requestId = "40000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const api = {
      list: vi.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([grant]),
      grant: vi.fn().mockResolvedValue(undefined),
      revoke: vi.fn(),
    };

    await act(async () => root.render(<FaeAccessPanel account={owner} api={api} />));
    await settle();
    const name = container.querySelector("input[aria-label='花名']") as HTMLInputElement;
    const submit = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "授予访问",
    ) as HTMLButtonElement;
    expect(container.querySelector("input[name='internal_user_id']")).toBeNull();
    expect(submit.disabled).toBe(true);

    await act(async () => inputValue(name, "天启"));
    expect(submit.disabled).toBe(false);
    await act(async () => submit.click());
    await settle();

    expect(api.grant).toHaveBeenCalledWith(owner, "天启", requestId);
    expect(container.textContent).toContain("授权成功");
  });

  it("keeps and reuses the same request id after an indeterminate grant", async () => {
    const requestId = "50000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const api = {
      list: vi.fn().mockResolvedValue([]),
      grant: vi.fn()
        .mockRejectedValueOnce(new ManagementMutationIndeterminate(requestId, {}))
        .mockResolvedValueOnce(undefined),
      revoke: vi.fn(),
    };

    await act(async () => root.render(<FaeAccessPanel account={owner} api={api} />));
    await settle();
    await act(async () => inputValue(
      container.querySelector("input[aria-label='花名']") as HTMLInputElement,
      "范闲",
    ));
    const submit = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "授予访问",
    ) as HTMLButtonElement;
    await act(async () => submit.click());
    await settle();

    expect(container.textContent).toContain("结果暂时无法确认");
    const retry = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "使用同一请求重试",
    ) as HTMLButtonElement;
    await act(async () => retry.click());
    await settle();

    expect(api.grant).toHaveBeenNthCalledWith(1, owner, "范闲", requestId);
    expect(api.grant).toHaveBeenNthCalledWith(2, owner, "范闲", requestId);
    expect(globalThis.crypto.randomUUID).toHaveBeenCalledTimes(1);
  });

  it("revokes the selected grant with its current row version", async () => {
    const requestId = "60000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const api = {
      list: vi.fn()
        .mockResolvedValueOnce([grant])
        .mockResolvedValueOnce([]),
      grant: vi.fn(),
      revoke: vi.fn().mockResolvedValue(undefined),
    };

    await act(async () => root.render(<FaeAccessPanel account={owner} api={api} />));
    await settle();
    const revoke = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "撤销访问",
    ) as HTMLButtonElement;
    await act(async () => revoke.click());
    await settle();

    expect(api.revoke).toHaveBeenCalledWith(owner, grant, requestId);
    expect(container.textContent).toContain("撤销成功");
  });
});
