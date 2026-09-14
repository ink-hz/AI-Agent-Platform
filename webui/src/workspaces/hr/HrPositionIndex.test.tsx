/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import type { HrPosition } from "../../hrTypes";
import { HrPositionIndex } from "./HrPositionIndex";


const account = {
  internal_user_id: "member", display_name: "HR", role: "member", departments: [],
  gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
} as Account;
const base = {
  department: "研发", locations: ["深圳"], internalStatus: "active" as const,
  rowVersion: 1, createdAt: "2026-09-04T00:00:00Z", updatedAt: "2026-09-04T00:00:00Z",
};
const official: HrPosition = {
  ...base, positionId: "11111111-1111-4111-8111-111111111111", sourceKind: "official_site",
  officialJobId: "J11014", title: "算法工程师", officialStatus: "active", sourceVersion: "sync-v2",
};
const manual: HrPosition = {
  ...base, positionId: "22222222-2222-4222-8222-222222222222", sourceKind: "manual",
  officialJobId: null, title: "3D 打印高级结构工程师", officialStatus: null, sourceVersion: null,
};
function api(overrides = {}) {
  return {
    listPositions: vi.fn().mockResolvedValue({ items: [official, manual], nextCursor: null }),
    listDrafts: vi.fn(),
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });


it("loads only existing positions into one list without draft actions", async () => {
 const client=api();
 await act(async()=>root.render(<HrPositionIndex account={account} api={client as never}/>));
 expect(client.listDrafts).not.toHaveBeenCalled();
 expect(container.querySelectorAll('.hr-pw-position-list article')).toHaveLength(2);
 expect(container.textContent).not.toMatch(/待确认|用对话新建岗位|确认新建|合并到岗位/);
 expect(container.querySelector(`a[href="/hr/positions/${official.positionId}"]`)).not.toBeNull();
});
it('reads every cursor and filters real positions by search and status',async()=>{
 const client=api({listPositions:vi.fn().mockResolvedValueOnce({items:[official],nextCursor:'next'}).mockResolvedValueOnce({items:[{...manual,internalStatus:'archived'}],nextCursor:null})});
 await act(async()=>root.render(<HrPositionIndex account={account} api={client as never}/>));
 expect(client.listPositions).toHaveBeenCalledTimes(2);
 const search=container.querySelector<HTMLInputElement>('input')!;
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(search,'3D');search.dispatchEvent(new Event('input',{bubbles:true}));});
 expect(container.querySelectorAll('article')).toHaveLength(1);
 expect(container.querySelector('article')?.textContent).toContain(manual.title);
 const select=container.querySelector('select')!;
 await act(async()=>{select.value='active';select.dispatchEvent(new Event('change',{bubbles:true}));});
 expect(container.querySelectorAll('article')).toHaveLength(0);
});
