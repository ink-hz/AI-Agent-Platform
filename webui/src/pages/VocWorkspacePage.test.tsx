/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VocWorkspacePage } from "./VocWorkspacePage";

const draft = {
  draft_id: "11111111-1111-4111-8111-111111111111", state: "collecting" as const,
  version: 1, source_text: "客户说设备发热",
  content: { customer: null, feedback: "设备发热", product_or_scenario: null, impact: null, evidence_basis: "employee_relay" as const, gaps: ["客户名称未知"] },
  submitted_voc_no: null, created_at: "2026-08-26T10:00:00Z", updated_at: "2026-08-26T10:00:00Z",
  assistant_message: "我先整理成了一份草稿，未知信息保留为空。",
};

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((node) => node.textContent === label);
  if (!(found instanceof HTMLButtonElement)) throw new Error(`missing button ${label}`);
  return found;
}

describe("VocWorkspacePage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.spyOn(crypto, "randomUUID").mockReturnValue("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("creates an editable draft and submits only after the explicit button", async () => {
    const api = {
      activeDraft: vi.fn().mockResolvedValue(null), listVocs: vi.fn().mockResolvedValue([]),
      createDraft: vi.fn().mockResolvedValue(draft), updateDraft: vi.fn(), cancelDraft: vi.fn(),
      submitDraft: vi.fn().mockResolvedValue({ voc_no: "VOC-20260826-001", revision: 1, already_submitted: false }),
      getVoc: vi.fn(), supplementVoc: vi.fn(),
    };
    await act(async () => root.render(<VocWorkspacePage csrfToken="csrf" api={api} />));
    const source = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='客户反馈']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(source, "客户说设备发热");
      source.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button(container, "整理成草稿").click());

    expect(container.querySelector<HTMLInputElement>("input[aria-label='反馈内容']")?.value).toBe("设备发热");
    expect(api.submitDraft).not.toHaveBeenCalled();
    await act(async () => button(container, "确认提交 VOC").click());
    expect(api.submitDraft).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("VOC-20260826-001");
  });

  it("persists visible edits before submitting the returned draft version", async () => {
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
      .mockReturnValueOnce("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
    const edited = {
      ...draft,
      version: 2,
      content: { ...draft.content, feedback: "设备发热并自动关机" },
    };
    const api = {
      activeDraft: vi.fn().mockResolvedValue(draft), listVocs: vi.fn().mockResolvedValue([]),
      createDraft: vi.fn(), updateDraft: vi.fn().mockResolvedValue(edited), cancelDraft: vi.fn(),
      submitDraft: vi.fn().mockResolvedValue({ voc_no: "VOC-20260826-002", revision: 1, already_submitted: false }),
      getVoc: vi.fn(), supplementVoc: vi.fn(),
    };
    await act(async () => root.render(<VocWorkspacePage csrfToken="csrf" api={api} />));
    const feedback = container.querySelector<HTMLInputElement>("input[aria-label='反馈内容']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(feedback, "设备发热并自动关机");
      feedback.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => button(container, "确认提交 VOC").click());

    expect(api.updateDraft).toHaveBeenCalledWith(
      draft.draft_id,
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      1,
      edited.content,
      "csrf",
    );
    expect(api.submitDraft).toHaveBeenCalledWith(
      draft.draft_id,
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      2,
      "csrf",
    );
    expect(api.updateDraft.mock.invocationCallOrder[0]).toBeLessThan(api.submitDraft.mock.invocationCallOrder[0]);
  });

  it("keeps employee text and request ID when the organizer is temporarily unavailable", async () => {
    const api = {
      activeDraft: vi.fn().mockResolvedValue(null), listVocs: vi.fn().mockResolvedValue([]),
      createDraft: vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(draft),
      updateDraft: vi.fn(), cancelDraft: vi.fn(), submitDraft: vi.fn(), getVoc: vi.fn(), supplementVoc: vi.fn(),
    };
    await act(async () => root.render(<VocWorkspacePage csrfToken="csrf" api={api} />));
    const source = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='客户反馈']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(source, "客户说设备发热");
      source.dispatchEvent(new Event("input", { bubbles: true }));
      button(container, "整理成草稿").click();
    });
    expect(source.value).toBe("客户说设备发热");
    await act(async () => button(container, "重新整理").click());
    expect(api.createDraft.mock.calls[0][0]).toBe(api.createDraft.mock.calls[1][0]);
  });

  it("starts a new idempotent action when employee text changes after a failed attempt", async () => {
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
      .mockReturnValueOnce("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
    const api = {
      activeDraft: vi.fn().mockResolvedValue(null), listVocs: vi.fn().mockResolvedValue([]),
      createDraft: vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(draft),
      updateDraft: vi.fn(), cancelDraft: vi.fn(), submitDraft: vi.fn(), getVoc: vi.fn(), supplementVoc: vi.fn(),
    };
    await act(async () => root.render(<VocWorkspacePage csrfToken="csrf" api={api} />));
    const source = container.querySelector<HTMLTextAreaElement>("textarea[aria-label='客户反馈']")!;
    const enter = async (value: string) => act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(source, value);
      source.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await enter("客户说设备发热");
    await act(async () => button(container, "整理成草稿").click());
    await enter("客户说设备发热并自动关机");
    await act(async () => button(container, "整理成草稿").click());

    expect(api.createDraft.mock.calls.map((call) => call[0])).toEqual([
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]);
  });
});
