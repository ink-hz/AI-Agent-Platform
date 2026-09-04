/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import type { HrPosition, HrPositionDraft } from "../../hrTypes";
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
const draft: HrPositionDraft = {
  draftId: "33333333-3333-4333-8333-333333333333", sourceKind: "historical_conversation",
  sourceKey: "history:one", sourceConversationId: null, title: "光学设计岗位",
  proposal: {}, evidence: { message_seq: 2 }, discoveryRuleVersion: "history-v1",
  state: "proposed", resolvedPositionId: null, rowVersion: 1,
  createdAt: base.createdAt, updatedAt: base.updatedAt,
};


function api(overrides = {}) {
  return {
    listPositions: vi.fn().mockResolvedValue({ items: [official, manual], nextCursor: null }),
    listDrafts: vi.fn().mockResolvedValue([draft]),
    confirmDraft: vi.fn().mockResolvedValue(manual),
    mergeDraft: vi.fn().mockResolvedValue({ ...draft, state: "merged" }),
    dismissDraft: vi.fn().mockResolvedValue({ ...draft, state: "dismissed" }),
    proposeDraft: vi.fn().mockResolvedValue(draft),
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


it("renders one position-first index with official, internal, and pending work", async () => {
  await act(async () => root.render(<HrPositionIndex account={account} api={api() as never} />));

  expect(container.querySelector("h1")?.textContent).toBe("岗位智能工作台");
  expect(container.textContent).toContain("官网岗位");
  expect(container.textContent).toContain("内部岗位");
  expect(container.textContent).toContain("待确认");
  expect(container.textContent).toContain("J11014");
  expect(container.textContent).toContain("官网版本 sync-v2");
  expect(container.querySelector(`a[href="/hr/positions/${official.positionId}"]`)).not.toBeNull();
  expect(container.textContent).not.toMatch(/北森|BOSS|猎聘|候选人漏斗/);
});


it("searches real loaded positions and offers all draft decisions", async () => {
  const client = api();
  await act(async () => root.render(<HrPositionIndex account={account} api={client as never} />));
  const search = container.querySelector<HTMLInputElement>('input[type="search"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "3D 打印");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });

  const positionGridText = [...container.querySelectorAll(".hr-position-grid")]
    .map((grid) => grid.textContent).join(" ");
  expect(positionGridText).toContain("3D 打印高级结构工程师");
  expect(positionGridText).not.toContain("算法工程师");
  expect([...container.querySelectorAll("button")].map((button) => button.textContent)).toEqual(
    expect.arrayContaining(["确认新建", "合并到岗位", "忽略"]),
  );
});


it("has explicit loading, retryable error, and honest empty states", async () => {
  let reject!: (error: Error) => void;
  const pending = new Promise((_resolve, rejected) => { reject = rejected; });
  const client = api({ listPositions: vi.fn(() => pending) });
  await act(async () => root.render(<HrPositionIndex account={account} api={client as never} />));
  expect(container.textContent).toContain("正在读取岗位");
  await act(async () => reject(new Error("offline")));
  expect(container.textContent).toContain("岗位数据暂时不可用");
  expect(container.textContent).toContain("重新加载");
});


it("starts new-position work from a natural-language request", async () => {
  const client = api();
  const start = vi.fn().mockResolvedValue({ conversationId: "conversation-new" });
  await act(async () => root.render(
    <HrPositionIndex account={account} api={client as never} startDraftConversation={start} />,
  ));
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "用对话新建岗位")?.click());
  const input = container.querySelector<HTMLTextAreaElement>("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(input, "需要一名懂喷嘴与挤出工艺的高级结构工程师");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "开始梳理")?.click());

  expect(client.proposeDraft).toHaveBeenCalledWith(expect.objectContaining({
    title: "需要一名懂喷嘴与挤出工艺的高级结构工程师",
  }), expect.any(String));
  expect(start).toHaveBeenCalledWith(expect.objectContaining({ draftId: draft.draftId }));
});


it("uses the default API client once instead of refetching after every render", async () => {
  const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
    const path = String(input);
    const body = path.includes("position-drafts")
      ? { items: [] }
      : { items: [], next_cursor: null };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);

  await act(async () => {
    root.render(<HrPositionIndex account={account} />);
    await new Promise((resolve) => setTimeout(resolve, 25));
  });

  expect(fetchMock).toHaveBeenCalledTimes(2);
});


it("requires an explicit visible target before merging a draft", async () => {
  const third = {
    ...manual,
    positionId: "44444444-4444-4444-8444-444444444444",
    title: "光学工程师",
  };
  const client = api({
    listPositions: vi.fn().mockResolvedValue({
      items: [official, manual, third], nextCursor: null,
    }),
  });
  await act(async () => root.render(
    <HrPositionIndex account={account} api={client as never} />,
  ));

  const merge = [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "合并到岗位")!;
  expect(merge.disabled).toBe(true);
  const select = container.querySelector<HTMLSelectElement>(
    `select[aria-label="选择 ${draft.title} 的合并目标"]`,
  )!;
  expect(select).not.toBeNull();
  expect(select.textContent).toContain("光学工程师");
  expect(select.textContent).toContain(third.positionId);

  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")
      ?.set?.call(select, third.positionId);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(merge.disabled).toBe(false);
  await act(async () => merge.click());

  expect(client.mergeDraft).toHaveBeenCalledWith(
    draft.draftId, third.positionId, draft.rowVersion, expect.any(String),
  );
});


it("loads every position page instead of hiding records after the first page", async () => {
  const later = {
    ...manual,
    positionId: "55555555-5555-4555-8555-555555555555",
    title: "第二页光学岗位",
  };
  const listPositions = vi.fn()
    .mockResolvedValueOnce({ items: [official], nextCursor: "page-2" })
    .mockResolvedValueOnce({ items: [later], nextCursor: null });
  await act(async () => root.render(<HrPositionIndex
    account={account} api={api({ listPositions }) as never}
  />));

  expect(listPositions).toHaveBeenNthCalledWith(
    1, { limit: 100 }, expect.any(AbortSignal),
  );
  expect(listPositions).toHaveBeenNthCalledWith(
    2, { limit: 100, cursor: "page-2" }, expect.any(AbortSignal),
  );
  expect(container.textContent).toContain("第二页光学岗位");
});


it("reuses draft and conversation idempotency after a partial new-position failure", async () => {
  const client = api();
  const start = vi.fn()
    .mockRejectedValueOnce(new Error("temporary"))
    .mockResolvedValueOnce({ conversationId: "conversation-new" });
  await act(async () => root.render(
    <HrPositionIndex account={account} api={client as never} startDraftConversation={start} />,
  ));
  await act(async () => [...container.querySelectorAll("button")]
    .find((button) => button.textContent === "用对话新建岗位")?.click());
  const input = container.querySelector<HTMLTextAreaElement>("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")
      ?.set?.call(input, "新岗位需求");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const submit = () => [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "开始梳理")!;
  await act(async () => submit().click());
  await act(async () => submit().click());

  expect(client.proposeDraft).toHaveBeenCalledTimes(2);
  expect(client.proposeDraft.mock.calls[0][1]).toBe(client.proposeDraft.mock.calls[1][1]);
  expect(start.mock.calls[0][0].requestId).toBe(start.mock.calls[1][0].requestId);
});
