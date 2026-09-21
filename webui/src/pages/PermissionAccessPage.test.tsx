/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Account } from "../auth";
import { PermissionAccessPage, type PermissionSection } from "./PermissionAccessPage";
import { PlatformSidebar } from "../platform/PlatformSidebar";
const owner: Account = { internal_user_id: "owner", display_name: "合成所有者", role: "platform_owner", departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf" };
let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
it.each(["fae", "voc", "partners"] as PermissionSection[])("does not load %s permissions for non-owners", async section => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<PermissionAccessPage account={{ ...owner, role: "platform_admin" }} section={section} />));
  expect(container.textContent).toContain("无权访问"); expect(fetchMock).not.toHaveBeenCalled();
});
it.each(["fae", "voc"] as PermissionSection[])("mounts only the selected %s authorization", async section => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ grants: [] })));
  vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<PermissionAccessPage account={owner} section={section} />));
  expect(container.querySelector("h1")?.textContent).toBe("账号与权限");
  expect(container.querySelector("nav[aria-label='权限分类'] button[aria-pressed='true']")?.textContent).toBe(section === "fae" ? "FAE 权限" : "VOC 权限");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/v1/manage/${section}-workbench/grants`);
});
it("groups every permission route under account permissions", async () => {
  await act(async () => root.render(<PlatformSidebar route={{ name: "admin-permissions", section: "voc" }} account={owner} collapsed={false} readOnly={false} />));
  expect(container.querySelector("a[aria-label='技术支持权限']")).toBeNull();
  expect(container.querySelector("a[aria-label='客户洞察权限']")).toBeNull();
  expect(container.querySelector("a[href='/admin/identity']")?.textContent).toContain("账号与权限");
  expect(container.querySelector("a[href='/admin/identity']")?.getAttribute("aria-current")).toBe("page");
  await act(async () => root.render(<PlatformSidebar route={{ name: "admin-identity" }} account={{ ...owner, role: "platform_admin" }} collapsed={false} readOnly={false} />));
  expect(container.querySelector("a[aria-label='技术支持权限']")).toBeNull();
});
it("preserves observer management on its secondary page", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [{ internal_user_id: "viewer", display_name: "合成观察者", role: "management_viewer", status: "active", scopes: ["agent-one"] }] })));
  vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<PermissionAccessPage account={{ ...owner, role: "platform_admin" }} section="observers" />));
  expect(container.querySelector("h1")?.textContent).toBe("账号与权限");
  expect(container.querySelector("nav[aria-label='权限分类'] button[data-section='fae']")).toBeNull();
  expect(container.textContent).toContain("撤销 agent-one");
  expect(container.querySelector<HTMLButtonElement>(".identity-users button")?.disabled).toBe(true);
  const reason = container.querySelector("input[aria-label='变更原因']") as HTMLInputElement;
  await act(async () => { reason.value = "批准"; reason.dispatchEvent(new Event("input", { bubbles: true })); });
  expect(container.querySelector<HTMLButtonElement>(".identity-users button")?.disabled).toBe(false);
});
