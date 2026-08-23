/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import { AccountPage } from "./AccountPage";


const account: Account = {
  internal_user_id: "62a31b32-2a92-47d4-9f79-f0c61bca12aa",
  display_name: "苍渊",
  role: "platform_owner",
  departments: ["项目管理部"],
  gender: "male",
  observation_agent_ids: ["ai-fae-agent"],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf-memory-only",
};


describe("AccountPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("shows only safe account information and logs out with in-memory CSRF", async () => {
    const onLogout = vi.fn().mockResolvedValue(undefined);
    await act(async () => root.render(<AccountPage account={account} onLogout={onLogout} />));
    expect(container.textContent).toContain("苍渊");
    expect(container.textContent).toContain("平台所有者");
    expect(container.textContent).not.toContain(account.csrf_token);
    await act(async () => container.querySelector("button")?.click());
    expect(onLogout).toHaveBeenCalledWith(account.csrf_token);
  });

  it("labels a platform administrator account", async () => {
    await act(async () => root.render(<AccountPage
      account={{ ...account, role: "platform_admin", display_name: "管理员" }}
      onLogout={vi.fn().mockResolvedValue(undefined)}
    />));

    expect(container.textContent).toContain("平台管理员");
  });
});
