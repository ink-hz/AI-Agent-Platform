/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "./auth";

const fixtures = vi.hoisted(() => {
  const account = {
    internal_user_id: "member", display_name: "HR", role: "member", departments: [],
    gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
    hard_stale_read_only: false, csrf_token: "csrf",
  } as Account;
  const conversationId = "33333333-3333-4333-8333-333333333333";
  const positionId = "44444444-4444-4444-8444-444444444444";
  const positionPackage = {
    draftId: "11111111-1111-4111-8111-111111111111",
    draftVersionId: "22222222-2222-4222-8222-222222222222",
    conversationId, versionNumber: 2, title: "视觉算法工程师",
    modules: { mission: { text: "岗位需求" }, jd: { text: "JD" }, jr: { text: "JR" } },
    rowVersion: 3, createdAt: "2026-09-04T01:00:00Z", updatedAt: "2026-09-04T02:00:00Z",
  };
  const api = {
    positionPackage: vi.fn().mockResolvedValue(positionPackage),
    confirmPositionPackage: vi.fn().mockResolvedValue({ positionId, conversationId, contextVersionId: "context-1" }),
  };
  return { account, api, conversationId, positionId, positionPackage };
});

vi.mock("./auth", async (original) => ({
  ...await original<typeof import("./auth")>(),
  identityShellEnabled: () => true,
  loadAccount: vi.fn().mockResolvedValue(fixtures.account),
}));
vi.mock("./AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div data-testid="app-shell"><input defaultValue="preserved" />{children}</div>,
}));
vi.mock("./accessEventReporter", () => ({ AccessEventReporter: () => null }));
vi.mock("./workspaces/hr/HrWorkspacePage", async () => {
  const { HrConversationOutcomePanel } = await import("./workspaces/hr/HrConversationOutcomePanel");
  return {
    HrWorkspacePage: ({ conversationId, positionId }: { conversationId?: string; positionId?: string }) => positionId
      ? <p data-testid="confirmed-route">{positionId}:{conversationId}</p>
      : <HrConversationOutcomePanel api={fixtures.api} conversationId={conversationId} csrfToken="csrf" />,
  };
});

import App from "./App";

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  window.history.replaceState({}, "", `/hr/conversations/${fixtures.conversationId}`);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

it("confirms through App routing without a document reload", async () => {
  const pushed = vi.spyOn(window.history, "pushState");
  await act(async () => root.render(<App />));
  const shell = container.querySelector('[data-testid="app-shell"]');
  const preservedInput = shell?.querySelector("input");

  await act(async () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "确认并加入岗位库")?.click());

  expect(pushed).toHaveBeenCalledWith({}, "", `/hr/positions/${fixtures.positionId}/conversations/${fixtures.conversationId}`);
  expect(window.location.pathname).toBe(`/hr/positions/${fixtures.positionId}/conversations/${fixtures.conversationId}`);
  expect(container.querySelector('[data-testid="confirmed-route"]')?.textContent)
    .toBe(`${fixtures.positionId}:${fixtures.conversationId}`);
  expect(container.querySelector('[data-testid="app-shell"]')).toBe(shell);
  expect(container.querySelector('[data-testid="app-shell"] input')).toBe(preservedInput);
});
