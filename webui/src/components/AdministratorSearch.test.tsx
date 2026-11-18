/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { AdministratorSearch } from "./AdministratorSearch";

const firstAccount = "20000000-0000-4000-8000-000000000001";
const secondAccount = "20000000-0000-4000-8000-000000000002";
const secondDirectory = "10000000-0000-4000-8000-000000000002";
const user = { directory_member_id: "10000000-0000-4000-8000-000000000001", directory_generation_id: "40000000-0000-4000-8000-000000000001", internal_user_id: firstAccount, assignment_user_id: firstAccount, display_name: "同名", departments: ["研发"] };
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const response = (users = [user], truncated = false) => new Response(JSON.stringify({ users, truncated }), { status: 200 });
beforeEach(() => {
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
async function type(value: string) {
  const input = container.querySelector("input")!;
  await act(async () => { input.value = value; input.dispatchEvent(new Event("input", { bubbles: true })); });
}
async function submit() {
  await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}
async function render(disabled = false, excludedIds: string[] = []) {
  const select = vi.fn(); const close = vi.fn();
  await act(async () => root.render(<AdministratorSearch disabled={disabled} excludedIds={excludedIds} onSelect={select} onClose={close} />));
  return { select, close };
}
it("does not load people on mount or blank searches", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  await render(); await type("   "); await submit();
  expect(fetchMock).not.toHaveBeenCalled();
});
it("searches by display name and selects the exact account among duplicate names", async () => {
  const other = { ...user, directory_member_id: secondDirectory, internal_user_id: secondAccount, assignment_user_id: secondAccount, departments: ["市场"] };
  const fetchMock = vi.fn().mockResolvedValue(response([user, other])); vi.stubGlobal("fetch", fetchMock);
  const { select } = await render(); await type(" 同名 "); await submit();
  expect(String(fetchMock.mock.calls[0][0])).toBe("/api/v1/manage/users?view=candidates_roster_v2&q=%E5%90%8C%E5%90%8D&limit=20");
  expect(container.textContent).toContain("研发"); expect(container.textContent).toContain("市场");
  await act(async () => container.querySelectorAll("article button")[1].dispatchEvent(new MouseEvent("click", { bubbles: true })));
  expect(select).toHaveBeenCalledWith(other);
});
it("blocks indistinguishable names without exposing account IDs", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([user, { ...user, directory_member_id: secondDirectory, internal_user_id: secondAccount, assignment_user_id: secondAccount }])));
  await render(); await type("同名"); await submit();
  expect([...container.querySelectorAll("article button")].every(button => button.hasAttribute("disabled"))).toBe(true);
  expect(container.textContent).toContain("请先核实通讯录身份");
  expect(container.textContent).not.toContain(firstAccount);
});
it("clears old matches immediately and ignores late responses", async () => {
  let resolveOld!: (value: Response) => void;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { resolveOld = resolve; }))
    .mockResolvedValueOnce(response([{ ...user, display_name: "新查询" }]));
  vi.stubGlobal("fetch", fetchMock);
  await render(); await type("旧查询"); await submit(); await type("新查询"); await submit();
  await act(async () => resolveOld(response([{ ...user, display_name: "旧查询" }])));
  expect(container.querySelector("article strong")?.textContent).toBe("新查询");
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  await type("第三次"); expect(container.querySelector("article")).toBeNull();
});
it("shows errors and truncation, and excludes accounts already added", async () => {
  const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce(response([user], true));
  vi.stubGlobal("fetch", fetchMock);
  await render(false, [user.internal_user_id]); await type("同名"); await submit();
  expect(container.textContent).toContain("搜索失败"); await submit();
  expect(container.textContent).toContain("匹配较多"); expect(container.querySelector("article")).toBeNull();
});
it("does not search when administrator mutation is blocked", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  await render(true); await type("同名"); await submit(); expect(fetchMock).not.toHaveBeenCalled();
});

it("requires a narrower search before selecting a truncated result", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([user], true)));
  await render(); await type("同"); await submit();
  expect(container.querySelector("article button")?.hasAttribute("disabled")).toBe(true);
});

it("selects an unlinked employee by the exact roster entry and prospective account", async () => {
  const match = {...user, internal_user_id: null, display_name: "合成员工", departments: ["制造部"]};
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({users: [match], truncated: false}))));
  const {select} = await render(); await type("合成员工"); await submit();
  const row = container.querySelector("article");
  expect(row?.textContent).toContain("合成员工");
  expect(row?.textContent).toContain("制造部");
  await act(async () => row?.querySelector("button")?.click());
  expect(select).toHaveBeenCalledWith(match);
});

it("blocks selection of a linked account when an unlinked namesake has the same department", async () => {
  const namesake = {...user, directory_member_id: secondDirectory, internal_user_id: null, assignment_user_id: secondAccount};
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({users: [user, namesake], truncated: false}))));
  const {select} = await render(); await type(user.display_name); await submit();
  expect([...container.querySelectorAll<HTMLButtonElement>("article button")].map(button => button.disabled)).toEqual([true, true]);
  expect(select).not.toHaveBeenCalled();
});
