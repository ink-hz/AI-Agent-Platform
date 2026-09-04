/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "./auth";

const { positionId, account } = vi.hoisted(() => ({ positionId: "00000000-0000-4000-8000-000000000001", account: {
  internal_user_id: "member", display_name: "HR", role: "member", departments: [],
  gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
} as Account }));

vi.mock("./auth", async (original) => ({
  ...await original<typeof import("./auth")>(),
  identityShellEnabled: () => true,
  loadAccount: vi.fn().mockResolvedValue(account),
}));
vi.mock("./router", async (original) => ({
  ...await original<typeof import("./router")>(),
  useRoute: () => ({ name: "hr-position-section", positionId, section: "candidates" }),
}));
vi.mock("./workspaces/hr/HrWorkspacePage", () => ({
  HrWorkspacePage: ({ positionId: received, section }: { positionId: string; section?: string }) => <p>{received}:{section}</p>,
}));

import App from "./App";

let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("authorizes and restores an HR position section deep link for a member", async () => {
  await act(async () => root.render(<App />));
  expect(container.textContent).toContain(`${positionId}:candidates`);
  expect(container.textContent).not.toContain("无权访问");
  expect(container.textContent).not.toContain("页面不存在");
});
