/** @vitest-environment jsdom */
import { afterEach, expect, it, vi } from "vitest";
import { listAdministratorUsers, searchAdministratorCandidates } from "./administratorDirectory";
const admin = { internal_user_id: "one", display_name: "合成管理员", role: "platform_admin", status: "active", scopes: [], departments: ["合成部门"] };
afterEach(() => vi.unstubAllGlobals());
it("keeps the administrator projection small and typed", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [admin], truncated: false })));
  vi.stubGlobal("fetch", fetchMock);
  expect(await listAdministratorUsers()).toEqual([admin]);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/manage/users?view=administrators");
});
it.each([
  { users: [admin], truncated: true },
  { users: [{ ...admin, role: "member" }], truncated: false },
  { users: [{ ...admin, mobile: "private" }], truncated: false },
  { users: [{ ...admin, departments: null }], truncated: false },
  { users: [admin] },
])("rejects incomplete or unexpected administrator projections %j", async body => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body))));
  await expect(listAdministratorUsers()).rejects.toThrow("administrator response invalid");
});
it("rejects privileged or inactive search candidates", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [admin], truncated: false }))));
  await expect(searchAdministratorCandidates("合成")).rejects.toThrow("administrator response invalid");
});
it("does not fetch blank candidate queries", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  expect(await searchAdministratorCandidates(" ")).toEqual({ users: [], truncated: false }); expect(fetchMock).not.toHaveBeenCalled();
});
