/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { FaeManagementWorkspace } from "./FaeManagementWorkspace";


vi.mock("../../pages/FaeOverviewPage", () => ({ FaeOverviewPage: () => <p>FAE overview</p> }));
vi.mock("../../pages/FaeSessionsPage", () => ({ FaeSessionsPage: () => <p>FAE sessions</p> }));
vi.mock("../../pages/FaeSessionDetailPage", () => ({ FaeSessionDetailPage: () => <p>FAE session</p> }));
vi.mock("../../pages/FaeIssuesPage", () => ({ FaeIssuesPage: () => <p>FAE issues</p> }));
vi.mock("../../pages/FaeReportsPage", () => ({ FaeReportsPage: () => <p>FAE reports</p> }));


const hardStaleOwner: Account = {
  internal_user_id: "owner",
  display_name: "苍渊",
  role: "platform_owner",
  departments: [],
  gender: null,
  observation_agent_ids: [],
  workspace_scopes: ["fae_workbench"],
  directory_freshness: "hard_stale",
  hard_stale_read_only: true,
  csrf_token: "csrf",
};


describe("FaeManagementWorkspace", () => {
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
    vi.restoreAllMocks();
  });

  it("owns the hard-stale notice inside ordinary FAE management pages", async () => {
    await act(async () => root.render(
      <FaeManagementWorkspace account={hardStaleOwner} route={{ name: "fae-manage-overview" }} />,
    ));

    expect(container.querySelector(".fae-management-readonly")?.textContent).toContain("只读访问");
  });

  it("does not duplicate the governance page's dedicated read-only notice", async () => {
    await act(async () => root.render(
      <FaeManagementWorkspace account={hardStaleOwner} route={{ name: "fae-manage-issues" }} />,
    ));

    expect(container.querySelector(".fae-management-readonly")).toBeNull();
  });
});
