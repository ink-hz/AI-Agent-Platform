/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VocManagementPage } from "./VocManagementPage";
import type {
  VocAdminApi,
  VocAdminDetail,
  VocAdminPage,
  VocAdminSummary,
} from "../vocAdminApi";

const summary = (vocNo: string, content = "设备连续运行三小时后明显发热"): VocAdminSummary => ({
  voc_no: vocNo,
  submitter_internal_user_id: "11111111-1111-4111-8111-111111111111",
  submitter_name: "苍渊",
  source: "platform",
  latest_content: content,
  revision: 2,
  analysis_status: "claimed",
  created_at: "2026-08-26T09:30:00Z",
  updated_at: "2026-08-26T09:35:00Z",
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fakeApi(): VocAdminApi & {
  list: ReturnType<typeof vi.fn>;
  detail: ReturnType<typeof vi.fn>;
  submitters: ReturnType<typeof vi.fn>;
} {
  return {
    list: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    detail: vi.fn().mockResolvedValue({ ...summary("VOC-20260826-001"), entries: [] }),
    submitters: vi.fn().mockResolvedValue([
      { internal_user_id: "11111111-1111-4111-8111-111111111111", display_name: "苍渊" },
    ]),
  };
}

function input(container: HTMLElement, label: string): HTMLInputElement | HTMLSelectElement {
  const node = Array.from(container.querySelectorAll("label")).find((item) => item.textContent?.includes(label));
  const control = node?.querySelector("input, select");
  if (!(control instanceof HTMLInputElement) && !(control instanceof HTMLSelectElement)) throw new Error(`missing ${label}`);
  return control;
}

async function change(control: HTMLInputElement | HTMLSelectElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype,
      "value",
    )?.set;
    setter?.call(control, value);
    control.dispatchEvent(new Event("change", { bubbles: true }));
    control.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("VOC management page", () => {
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

  it("renders the overview and opens a complete read-only detail", async () => {
    const api = fakeApi();
    api.list.mockResolvedValue({
      items: [summary("VOC-20260826-001"), { ...summary("VOC-20260826-002", "客户现场自动关机"), source: "dingtalk", analysis_status: "succeeded", submitter_name: "历史同事" }],
      next_cursor: null,
    });
    const detail: VocAdminDetail = {
      ...summary("VOC-20260826-001"),
      entries: [
        { revision: 1, entry_type: "original", content: "设备发热", created_at: "2026-08-26T09:30:00Z" },
        { revision: 2, entry_type: "supplement", content: "持续三小时", created_at: "2026-08-26T09:35:00Z" },
      ],
    };
    api.detail.mockResolvedValue(detail);

    await act(async () => root.render(<VocManagementPage api={api} />));
    await act(async () => undefined);

    expect(container.textContent).toContain("VOC-20260826-001");
    expect(container.textContent).toContain("历史同事");
    expect(container.textContent).toContain("钉钉历史");
    expect(container.textContent).toContain("已分析");
    const row = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "VOC-20260826-001");
    await act(async () => row?.click());
    await act(async () => undefined);

    const drawer = container.querySelector('aside[aria-label="VOC 详情"]');
    expect(drawer?.textContent).toContain("设备发热");
    expect(drawer?.textContent).toContain("持续三小时");
    expect(Array.from(drawer?.querySelectorAll("button") || []).map((button) => button.textContent))
      .not.toEqual(expect.arrayContaining(["编辑", "删除", "分配", "补充"]));
    await act(async () => Array.from(drawer?.querySelectorAll("button") || []).find((button) => button.textContent === "关闭")?.click());
    expect(container.querySelector('aside[aria-label="VOC 详情"]')).toBeNull();
  });

  it("renders an explicit placeholder for attachment-only history", async () => {
    const api = fakeApi();
    const attachmentOnly = summary("VOC-20260826-003", "");
    api.list.mockResolvedValue({ items: [attachmentOnly], next_cursor: null });
    api.detail.mockResolvedValue({
      ...attachmentOnly,
      entries: [{
        revision: 1,
        entry_type: "original",
        content: "",
        created_at: "2026-08-26T09:30:00Z",
      }],
    });

    await act(async () => root.render(<VocManagementPage api={api} />));
    await act(async () => undefined);
    expect(container.textContent).toContain("仅包含附件，暂无文字内容");

    const row = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === attachmentOnly.voc_no);
    await act(async () => row?.click());
    await act(async () => undefined);
    expect(container.querySelector('aside[aria-label="VOC 详情"]')?.textContent)
      .toContain("仅包含附件，暂无文字内容");
  });

  it("submits mutually exclusive filters, converts date bounds, and appends more", async () => {
    const api = fakeApi();
    api.list
      .mockResolvedValueOnce({ items: [summary("VOC-20260826-001")], next_cursor: null })
      .mockResolvedValueOnce({ items: [summary("VOC-20260827-001")], next_cursor: "next" })
      .mockResolvedValueOnce({ items: [summary("VOC-20260827-002")], next_cursor: null });
    await act(async () => root.render(<VocManagementPage api={api} />));
    await act(async () => undefined);

    await change(input(container, "关键词"), "发热");
    await change(input(container, "平台提交人"), "11111111-1111-4111-8111-111111111111");
    await change(input(container, "历史钉钉提交人"), "历史同事");
    expect((input(container, "平台提交人") as HTMLSelectElement).value).toBe("");
    await change(input(container, "开始日期"), "2026-08-01");
    await change(input(container, "结束日期"), "2026-08-31");
    const form = container.querySelector("form");
    await act(async () => form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await act(async () => undefined);

    const expectedStart = new Date("2026-08-01T00:00:00").toISOString();
    const expectedEnd = new Date("2026-08-31T00:00:00");
    expectedEnd.setDate(expectedEnd.getDate() + 1);
    expect(api.list.mock.calls[1][0]).toMatchObject({
      query: "发热",
      submitterInternalUserId: null,
      legacySubmitterName: "历史同事",
      createdFrom: expectedStart,
      createdTo: expectedEnd.toISOString(),
      cursor: null,
    });
    const more = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "加载更多");
    await act(async () => more?.click());
    await act(async () => undefined);
    expect(api.list.mock.calls[2][0].cursor).toBe("next");
    expect(container.textContent).toContain("VOC-20260827-001");
    expect(container.textContent).toContain("VOC-20260827-002");
  });

  it("does not let an older list request replace newer filtered results", async () => {
    const api = fakeApi();
    const first = deferred<VocAdminPage>();
    const second = deferred<VocAdminPage>();
    api.list.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await act(async () => root.render(<VocManagementPage api={api} />));
    await change(input(container, "关键词"), "自动关机");
    await act(async () => container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await act(async () => second.resolve({ items: [summary("VOC-NEW-001", "自动关机")], next_cursor: null }));
    await act(async () => first.resolve({ items: [summary("VOC-OLD-001", "旧数据")], next_cursor: null }));

    expect(container.textContent).toContain("VOC-NEW-001");
    expect(container.textContent).not.toContain("VOC-OLD-001");
  });

  it("distinguishes empty states and aborts outstanding calls on unmount", async () => {
    const api = fakeApi();
    const pending = deferred<VocAdminPage>();
    api.list.mockReturnValue(pending.promise);
    await act(async () => root.render(<VocManagementPage api={api} />));
    const listSignal = api.list.mock.calls[0][1] as AbortSignal;
    const submitterSignal = api.submitters.mock.calls[0][0] as AbortSignal;
    await act(async () => root.unmount());
    expect(listSignal.aborted).toBe(true);
    expect(submitterSignal.aborted).toBe(true);
    root = createRoot(container);

    const emptyApi = fakeApi();
    await act(async () => root.render(<VocManagementPage api={emptyApi} />));
    await act(async () => undefined);
    expect(container.textContent).toContain("还没有 VOC 记录");
    await change(input(container, "关键词"), "不存在");
    await act(async () => container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await act(async () => undefined);
    expect(container.textContent).toContain("没有符合条件的 VOC");
  });
});
