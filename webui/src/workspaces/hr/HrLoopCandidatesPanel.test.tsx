/** @vitest-environment jsdom */
import { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrLoopError } from "../../hrLoopApi";
import { HrLoopCandidatesPanel } from "./HrLoopCandidatesPanel";
import {
  AttachmentApiError,
  beginAttachmentUpload,
  uploadAttachmentContent,
  completeAttachmentUpload,
  fetchConversationAttachment,
  issueAttachmentTicket,
} from "../../attachmentApi";
vi.mock("../../attachmentApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../attachmentApi")>()),
  beginAttachmentUpload: vi.fn(),
  uploadAttachmentContent: vi.fn(),
  completeAttachmentUpload: vi.fn(),
  fetchConversationAttachment: vi.fn(),
  issueAttachmentTicket: vi.fn(),
}));
const ref = {
  kind: "result",
  id: "result",
  revision: "exact",
  sha256: "a".repeat(64),
};
const review = {
  item_id: "one",
  batch_id: "batch",
  attachment_id: "a",
  state: "awaiting_review",
  row_version: 3,
  generation: 1,
  work_id: "w",
  result_ref: ref,
  error_code: null,
  failed_stage: null,
  profile_body: "模型草稿里的合成姓名与经历，仅供参考",
  parse_state: "ready",
  original_ref: { ...ref, kind: "material" },
  text_ref: { ...ref, kind: "material" },
  coverage_complete: false,
  coverage_notes: ["扫描图片未覆盖"],
  unread_ranges: [[10, 20]],
  work_state: "completed",
};
function api() {
  return {
    batches: vi.fn().mockResolvedValue({ items: [] }),
    batch: vi
      .fn()
      .mockResolvedValue({
        batch_id: "batch",
        position_id: null,
        items: [review],
      }),
    createBatch: vi
      .fn()
      .mockResolvedValue({ batch_id: "batch", item_ids: ["one"] }),
    candidates: vi.fn().mockResolvedValue({ items: [] }),
    candidate: vi.fn(),
    item: vi.fn(),
    retry: vi
      .fn()
      .mockResolvedValue({ item_id: "one", state: "queued", row_version: 4 }),
    confirm: vi
      .fn()
      .mockResolvedValue({
        item_id: "one",
        candidate_id: "confirmed-candidate",
        document_id: "doc",
        state: "confirmed",
        row_version: 4,
      }),
  };
}
let root: ReturnType<typeof createRoot>, el: HTMLDivElement;
beforeEach(() => {
  el = document.createElement("div");
  document.body.append(el);
  root = createRoot(el);
  (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
  vi.mocked(beginAttachmentUpload).mockImplementation(
    async (_c, file) =>
      ({ uploadId: file.name, attachmentId: file.name }) as never,
  );
  vi.mocked(uploadAttachmentContent).mockResolvedValue({} as never);
  vi.mocked(completeAttachmentUpload).mockResolvedValue({} as never);
  vi.mocked(fetchConversationAttachment).mockImplementation(
    async (id) =>
      ({ attachmentId: id, displayName: id, state: "ready" }) as never,
  );
  vi.mocked(issueAttachmentTicket).mockResolvedValue({
    contentPath: "/api/v1/attachments/content/local-ticket",
  } as never);
});
afterEach(async () => {
  await act(async () => root.unmount());
  el.remove();
  vi.clearAllMocks();
  vi.useRealTimers();
});
function button(label: string, within: ParentNode = el) {
  return [...within.querySelectorAll("button")].find(
    (b) => b.textContent === label,
  )!;
}
async function input(label: string, value: string, within: ParentNode = el) {
  await act(async () => {
    const element = within.querySelector<
      HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
    >(`[aria-label="${label}"]`)!;
    const proto =
      element.tagName === "TEXTAREA"
        ? HTMLTextAreaElement.prototype
        : element.tagName === "SELECT"
          ? HTMLSelectElement.prototype
          : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(element, value);
    element.dispatchEvent(
      new Event(element.tagName === "SELECT" ? "change" : "input", {
        bubbles: true,
      }),
    );
  });
}
async function render(client: ReturnType<typeof api>, extras = {}) {
  const onAccessError = vi.fn(),
    onOpenWork = vi.fn();
  await act(async () =>
    root.render(
      <HrLoopCandidatesPanel
        api={client as never}
        csrf="csrf"
        disabled={false}
        budgetProfile="approved"
        onClose={() => {}}
        onAccessError={onAccessError}
        onOpenWork={onOpenWork}
        {...extras}
      />,
    ),
  );
  return { onAccessError, onOpenWork };
}
async function openBatch(client: ReturnType<typeof api>) {
  client.batches.mockResolvedValue({
    items: [{ batch_id: "batch", created_at: "2026-09-11T08:00:00Z" }],
  });
  await render(client);
  await act(async () => button("查看批次 1").click());
}
it("retains a failed sibling upload and creates a batch only from ready files", async () => {
  const client = api();
  vi.mocked(uploadAttachmentContent).mockImplementation(async (id) => {
    if (id === "bad.pdf") throw new TypeError("network");
    return {} as never;
  });
  await render(client);
  await act(async () => {
    const field = el.querySelector('input[type="file"]')!;
    Object.defineProperty(field, "files", {
      value: [
        new File(["synthetic"], "good.pdf"),
        new File(["synthetic"], "bad.pdf"),
      ],
    });
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(el.textContent).toContain("bad.pdf");
  expect(button("重试上传")).toBeDefined();
  await act(async () => button("登记已就绪材料").click());
  expect(client.createBatch.mock.calls[0][0].attachment_ids).toEqual([
    "good.pdf",
  ]);
  expect(client.createBatch.mock.calls[0][0].budget_profile).toBe("approved");
  expect(el.textContent).toContain("bad.pdf");
  expect(client.confirm).not.toHaveBeenCalled();
});
it("requires human fields and limitations review while model prose remains reference only", async () => {
  const client = api();
  await openBatch(client);
  const form = el.querySelector('[aria-label="人工核对材料"]')!;
  expect(
    form.querySelector<HTMLInputElement>('[aria-label="姓名"]')?.value,
  ).toBe("");
  expect(
    form.querySelector<HTMLTextAreaElement>('[aria-label="已审阅摘要"]')?.value,
  ).toBe("");
  expect(el.textContent).toContain("扫描图片未覆盖");
  expect(el.textContent).toContain("仍有 1 段正文未读");
  await input("姓名", "合成同名", form);
  await input("已审阅摘要", "人工已核对", form);
  expect(button("确认新建候选人", form).disabled).toBe(true);
  await act(async () =>
    form.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(),
  );
  await act(async () => button("确认新建候选人", form).click());
  expect(client.confirm).toHaveBeenCalledWith(
    "one",
    {
      expected_row_version: 3,
      result_ref: ref,
      display_name: "合成同名",
      summary: "人工已核对",
      decision: { kind: "create" },
      reviewed_limitations: true,
    },
    expect.any(String),
  );
});
it("links only to an explicitly selected identity even when names match", async () => {
  const client = api();
  client.candidates.mockResolvedValue({
    items: [
      {
        candidate_id: "person-a",
        display_name: "合成同名",
        summary: "第一位的经历",
        available: true,
      },
      {
        candidate_id: "person-b",
        display_name: "合成同名",
        summary: "另一位的经历",
        available: true,
      },
    ],
  });
  await openBatch(client);
  const form = el.querySelector('[aria-label="人工核对材料"]')!;
  await input("姓名", "合成同名", form);
  await input("已审阅摘要", "本文件核对内容", form);
  await input("建档方式", "link_existing", form);
  expect(button("确认关联已有候选人", form).disabled).toBe(true);
  await input("已有候选人", "person-b", form);
  await act(async () =>
    form.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(),
  );
  await act(async () => button("确认关联已有候选人", form).click());
  expect(client.confirm.mock.calls[0][1].decision).toEqual({
    kind: "link_existing",
    candidate_id: "person-b",
  });
});
it("labels the default processing gate as pending and does not fabricate a review", async () => {
  const client = api();
  client.batch.mockResolvedValue({
    batch_id: "batch",
    position_id: null,
    items: [
      {
        ...review,
        state: "failed",
        profile_body: null,
        result_ref: null,
        error_code: "processing_not_authorized",
        failed_stage: "profile",
      },
    ],
  });
  await openBatch(client);
  expect(el.textContent).toContain("处理权限待开通");
  expect(el.querySelector('[aria-label="人工核对材料"]')).toBeNull();
  expect(client.confirm).not.toHaveBeenCalled();
});
it("polls pending upload metadata without registering or uploading bytes again", async () => {
  vi.useFakeTimers();
  const client = api();
  vi.mocked(fetchConversationAttachment).mockResolvedValueOnce({
    attachmentId: "pending.pdf",
    displayName: "pending.pdf",
    state: "scanning",
  } as never);
  await render(client);
  await act(async () => {
    const field = el.querySelector('input[type="file"]')!;
    Object.defineProperty(field, "files", {
      value: [new File(["synthetic"], "pending.pdf")],
    });
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(button("登记已就绪材料").disabled).toBe(true);
  expect(client.createBatch).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(button("登记已就绪材料").disabled).toBe(false);
  expect(beginAttachmentUpload).toHaveBeenCalledOnce();
  expect(uploadAttachmentContent).toHaveBeenCalledOnce();
  expect(completeAttachmentUpload).toHaveBeenCalledOnce();
});
it("retries only the failed file with its exact stage and row version", async () => {
  const client = api();
  client.batch.mockResolvedValue({
    batch_id: "batch",
    position_id: null,
    items: [
      review,
      {
        ...review,
        item_id: "failed-two",
        state: "failed",
        row_version: 7,
        profile_body: null,
        result_ref: null,
        error_code: "parse_failed",
        failed_stage: "parse",
      },
    ],
  });
  await openBatch(client);
  await act(async () => button("重试正文解析").click());
  expect(client.retry).toHaveBeenCalledWith(
    "failed-two",
    { expected_row_version: 7, stage: "parse" },
    expect.any(String),
  );
  expect(client.retry).toHaveBeenCalledOnce();
  expect(client.confirm).not.toHaveBeenCalled();
});
it("keeps confirmation identity and human fields after a lost response and revision conflict", async () => {
  const client = api();
  client.confirm
    .mockRejectedValueOnce(new TypeError("network"))
    .mockRejectedValueOnce(new HrLoopError(409, "revision_conflict", {}));
  await openBatch(client);
  const form = el.querySelector('[aria-label="人工核对材料"]')!;
  await input("姓名", "合成名称", form);
  await input("已审阅摘要", "人工核对摘要", form);
  await act(async () =>
    form.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(),
  );
  await act(async () => button("确认新建候选人", form).click());
  await act(async () => button("确认新建候选人", form).click());
  expect(client.confirm.mock.calls[0]).toEqual(client.confirm.mock.calls[1]);
  expect(el.textContent).toContain(
    "内容已更新，请重新加载状态并核对后再提交。",
  );
  expect(
    form.querySelector<HTMLInputElement>('[aria-label="姓名"]')?.value,
  ).toBe("合成名称");
  expect(el.textContent).not.toContain("材料已人工确认归档。");
});
it("clears private review and upload data when original access is revoked", async () => {
  const client = api();
  await openBatch(client);
  vi.mocked(issueAttachmentTicket).mockRejectedValueOnce(
    new AttachmentApiError(403),
  );
  await act(async () => button("获取原件").click());
  expect(el.querySelector('[aria-label="人工核对材料"]')).toBeNull();
  expect(el.textContent).not.toContain(review.profile_body);
  expect(
    el.querySelector<HTMLInputElement>('input[type="file"]')?.disabled,
  ).toBe(true);
});
it("exposes a ticketed original link and the actual work for a budget pause", async () => {
  const client = api();
  await openBatch(client);
  await act(async () => button("获取原件").click());
  expect(issueAttachmentTicket).toHaveBeenCalledWith("a", "download", "csrf");
  expect(el.querySelector("a")?.getAttribute("href")).toBe(
    "/api/v1/attachments/content/local-ticket",
  );
  client.batch.mockResolvedValue({
    batch_id: "batch",
    position_id: null,
    items: [
      {
        ...review,
        state: "profiling",
        work_state: "waiting_budget",
        profile_body: null,
        result_ref: null,
      },
    ],
  });
  const onOpenWork = vi.fn();
  await render(client, { onOpenWork });
  await act(async () => button("重新加载状态").click());
  expect(el.textContent).toContain("等待你为该文件追加额度");
  await act(async () => button("查看处理工作").click());
  expect(onOpenWork).toHaveBeenCalledWith("w");
});
it("suppresses a saved draft when the current attachment is unavailable", async () => {
  const client = api();
  vi.mocked(fetchConversationAttachment).mockRejectedValueOnce(
    new AttachmentApiError(410),
  );
  await openBatch(client);
  expect(el.textContent).toContain("原件已不可用或权限发生变化");
  expect(el.querySelector('[aria-label="人工核对材料"]')).toBeNull();
  expect(el.textContent).not.toContain(review.profile_body);
  expect(el.textContent).not.toContain("扫描图片未覆盖");
});
it("keeps upload signals usable after StrictMode effect cleanup", async () => {
  const client = api();
  await act(async () =>
    root.render(
      <StrictMode>
        <HrLoopCandidatesPanel
          api={client as never}
          csrf="csrf"
          disabled={false}
          budgetProfile="approved"
          onClose={() => {}}
          onAccessError={() => {}}
          onOpenWork={() => {}}
        />
      </StrictMode>,
    ),
  );
  await act(async () => {
    const field = el.querySelector('input[type="file"]')!;
    Object.defineProperty(field, "files", {
      value: [new File(["synthetic"], "strict.pdf")],
    });
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(vi.mocked(beginAttachmentUpload).mock.calls[0][3]?.aborted).toBe(
    false,
  );
});
it("does not restore a late batch after list access is revoked", async () => {
  const client = api();
  await openBatch(client);
  let finish: (value: unknown) => void = () => {};
  client.batch.mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  client.batches.mockRejectedValueOnce(new HrLoopError(403, "forbidden", {}));
  await act(async () => button("重新加载状态").click());
  await act(async () =>
    finish({ batch_id: "batch", position_id: null, items: [review] }),
  );
  expect(el.textContent).not.toContain(review.profile_body);
  expect(el.querySelector('[aria-label="人工核对材料"]')).toBeNull();
});
it("blocks upload, retry and review confirmation in read-only mode", async () => {
  const client = api();
  await openBatch(client);
  await render(client, { disabled: true });
  expect(
    el.querySelector<HTMLInputElement>('input[type="file"]')?.disabled,
  ).toBe(true);
  expect(el.querySelector("fieldset")?.disabled).toBe(true);
  expect(button("确认新建候选人").disabled).toBe(true);
  expect(client.confirm).not.toHaveBeenCalled();
  expect(client.createBatch).not.toHaveBeenCalled();
});
