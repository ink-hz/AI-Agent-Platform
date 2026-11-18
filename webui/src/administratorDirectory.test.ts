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

const unlinked = {
  directory_member_id: "cb06e6f3-7745-4a04-91fc-aaea17aa8877",
  directory_generation_id: "e2734b94-21ca-443a-afaa-0564d82c1c6b",
  internal_user_id: null, assignment_user_id: "9a63047d-1c04-4ea8-9699-273f7583f3a7",
  display_name: "员工", departments: [],
};
it.each([
  { ...unlinked, assignment_user_id: undefined },
  { ...unlinked, assignment_user_id: "invalid" },
  { ...unlinked, directory_member_id: "invalid" },
  { ...unlinked, directory_generation_id: undefined },
  { ...unlinked, directory_generation_id: "invalid" },
  { ...unlinked, internal_user_id: unlinked.directory_member_id },
])("rejects a roster candidate with an invalid assignment identity %j", async candidate => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ users: [candidate], truncated: false }))));
  await expect(searchAdministratorCandidates("员工")).rejects.toThrow("administrator response invalid");
});
