/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { HrLoopWorkspace } from "./HrLoopWorkspace";
import type { Account } from "../../auth";
import { HrLoopError } from "../../hrLoopApi";
import {
  beginAttachmentUpload,
  uploadAttachmentContent,
  completeAttachmentUpload,
} from "../../attachmentApi";
vi.mock("../../attachmentApi", () => ({
  beginAttachmentUpload: vi.fn(),
  uploadAttachmentContent: vi.fn(),
  completeAttachmentUpload: vi.fn(),
}));
const account = {
  internal_user_id: "u",
  display_name: "HR",
  csrf_token: "csrf",
  hard_stale_read_only: false,
} as Account;
const ref = { kind: "result", id: "r", revision: "v1", sha256: "a" };
const work = {
  work_id: "w",
  thread_id: "t",
  input_revision: 2,
  state: "waiting_user",
  phase: "research",
  answer_state: "partial",
  result_refs: [],
  pending_question_id: "q",
  block_reason: null,
  budget: {
    revision: 1,
    charged_calls: 3,
    charged_tokens: 1400,
    active_seconds: 20,
    limits: { model_calls: 5, total_tokens: 5000, active_seconds: 120 },
  },
  checkpoint: { open_questions: [], readings: [] },
};
function api(overrides = {}) {
  return {
    standard: vi.fn().mockRejectedValue(new HrLoopError(404, "not_found", {})),
    configuration: vi.fn().mockResolvedValue({
      budget_profile: "server-approved",
      budget_limits: work.budget.limits,
    }),
    knowledge: vi.fn().mockResolvedValue({ release_id: "release", items: [] }),
    intelligence: vi.fn().mockRejectedValue(new HrLoopError(410, "reference_unavailable", {})),
    threads: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    work: vi.fn().mockResolvedValue(work),
    input: vi.fn().mockResolvedValue({
      input_revision: 2,
      text: "previous question",
      objects: [],
      references: [],
    }),
    messages: vi.fn().mockResolvedValue({ items: [], next_after: 0 }),
    events: vi.fn().mockResolvedValue({ items: [], next_after: 0 }),
    results: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    submit: vi.fn().mockResolvedValue(work),
    append: vi.fn().mockResolvedValue(work),
    ...overrides,
  };
}
let el: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  el = document.createElement("div");
  document.body.append(el);
  root = createRoot(el);
  (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => {
  await act(async () => root.unmount());
  el.remove();
  vi.restoreAllMocks();
});
function button(label: string) {
  return [...el.querySelectorAll("button")].find(
    (b) => b.textContent === label,
  )!;
}
async function type(text: string) {
  await act(async () => {
    const input = el.querySelector("textarea")!;
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
it("starts without a position using server budget and retains key after network failure", async () => {
  const client = api({
    submit: vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(work),
  });
  await act(async () =>
    root.render(<HrLoopWorkspace account={account} api={client as never} />),
  );
  await type("梳理公开 JD");
  await act(async () => button("发送").click());
  await act(async () => button("发送").click());
  expect(client.submit).toHaveBeenCalledTimes(2);
  expect(client.submit.mock.calls[0][0]).toEqual({
    thread_id: null,
    text: "梳理公开 JD",
    objects: [],
    references: [],
    budget_profile: "server-approved",
  });
  expect(client.submit.mock.calls[0][1]).toBe(client.submit.mock.calls[1][1]);
});
it("restores references and appends only the continuation contract", async () => {
  const client = api({
    input: vi.fn().mockResolvedValue({
      input_revision: 2,
      text: "old",
      objects: [{ kind: "position", id: "p" }],
      references: [ref],
    }),
  });
  const positionApi = {
    position: vi
      .fn()
      .mockResolvedValue({ positionId: "p", title: "岗位", locations: [] }),
  };
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        positionApi={positionApi as never}
        initialWorkId="w"
      />,
    ),
  );
  await type("补充目标");
  await act(async () => button("发送").click());
  expect(client.append).toHaveBeenCalledWith(
    "w",
    {
      expected_input_revision: 2,
      text: "补充目标",
      objects: [{ kind: "position", id: "p" }],
      references: [ref],
      question_id: "q",
    },
    expect.any(String),
  );
});
it("requires individual proposal selection and preserves base revision on conflict", async () => {
  const proposal = {
    ref,
    kind: "standard_proposal",
    title: "待确认建议",
    body: "讨论正文",
    objects: [],
    changes: [
      {
        change_id: "one",
        action: "add",
        target_item_id: null,
        text: "选中要求",
      },
      {
        change_id: "two",
        action: "add",
        target_item_id: null,
        text: "未选要求",
      },
    ],
    base_standard_ref: { ...ref, kind: "standard", revision: "base" },
    basis: [],
  };
  const confirm = vi
    .fn()
    .mockRejectedValue(new HrLoopError(409, "revision_conflict", {}));
  const client = api({
    work: vi.fn().mockResolvedValue({ ...work, result_refs: [ref] }),
    result: vi.fn().mockResolvedValue(proposal),
    standard: vi.fn().mockResolvedValue({
      ref: { ...ref, revision: "new" },
      items: [{ item_id: "x", text: "当前标准" }],
      confirmed_at: "today",
    }),
    confirm,
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        positionApi={
          {
            position: vi.fn().mockResolvedValue({
              positionId: "p",
              title: "岗位",
              locations: [],
            }),
          } as never
        }
        initialPositionId="p"
        initialWorkId="w"
      />,
    ),
  );
  expect(button("确认选中条目").disabled).toBe(true);
  await act(async () =>
    el.querySelector<HTMLInputElement>("input[type=checkbox]")!.click(),
  );
  await act(async () => button("确认选中条目").click());
  expect(confirm.mock.calls[0][1]).toEqual({
    proposal_ref: ref,
    selected_change_ids: ["one"],
    expected_standard_revision: "base",
  });
  expect(el.textContent).toContain("重新阅读当前标准");
  await act(async () => button("重新阅读当前标准").click());
  expect(el.textContent).toContain("当前标准");
  expect(confirm).toHaveBeenCalledTimes(1);
});
it("discards a late previous work response after switching work", async () => {
  let resolve!: (v: unknown) => void;
  const client = api({
    work: vi.fn((id: string) =>
      id === "old"
        ? new Promise((r) => {
            resolve = r;
          })
        : Promise.resolve({ ...work, work_id: "new" }),
    ),
    messages: vi.fn((id: string) =>
      Promise.resolve({
        items: [
          {
            entry_id: id,
            seq: 1,
            kind: "assistant",
            body: id === "old" ? "私有旧内容" : "新工作",
            visibility: "available",
          },
        ],
        next_after: 1,
      }),
    ),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="old"
      />,
    ),
  );
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="new"
      />,
    ),
  );
  await act(async () => resolve({ ...work, work_id: "old" }));
  expect(el.textContent).not.toContain("私有旧内容");
  expect(el.textContent).toContain("新工作");
});
it("blocks writes in directory read only mode", async () => {
  const client = api();
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={{ ...account, hard_stale_read_only: true }}
        api={client as never}
      />,
    ),
  );
  await type("请求");
  expect(button("发送").disabled).toBe(true);
  expect(client.submit).not.toHaveBeenCalled();
});

it("opens exact method text before adding its reference", async () => {
  const methodRef = { ...ref, kind: "method" };
  const read = vi.fn().mockResolvedValue({
    ref: methodRef,
    text: "## 用途\n识别要求。\n## 边界\n需核对任务。\n## 来源\n公开文献",
  });
  const client = api({
    knowledge: vi.fn().mockResolvedValue({
      release_id: "released",
      items: [{ ref: methodRef, title: "要求校准", description: "用途与边界" }],
    }),
    method: read,
  });
  await act(async () =>
    root.render(<HrLoopWorkspace account={account} api={client as never} />),
  );
  expect(button("带此方法讨论")).toBeUndefined();
  await act(async () =>
    [...el.querySelectorAll("button")]
      .find((b) => b.textContent?.startsWith("要求校准"))!
      .click(),
  );
  expect(read).toHaveBeenCalledWith(methodRef);
  expect(el.textContent).toContain("需核对任务");
  await act(async () => button("带此方法讨论").click());
  await type("讨论");
  await act(async () => button("发送").click());
  expect(client.submit.mock.calls[0][0].references).toEqual([methodRef]);
});
it("only adds an explicit user budget addition using the current revision", async () => {
  const extend = vi.fn().mockResolvedValue({ ...work, state: "queued" });
  const client = api({
    work: vi.fn().mockResolvedValue({
      ...work,
      state: "waiting_budget",
      budget: { ...work.budget, revision: 7 },
    }),
    extend,
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="w"
      />,
    ),
  );
  expect(extend).not.toHaveBeenCalled();
  expect(button("按以上额度继续").disabled).toBe(true);
  await act(async () => {
    const n = el.querySelector("input[type=number]")!;
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(n, "2");
    n.dispatchEvent(new Event("input", { bubbles: true }));
    const r = el.querySelector(".hr-loop-budget input:not([type=number])")!;
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(r, "读完剩余材料");
    r.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("按以上额度继续").click());
  expect(extend).toHaveBeenCalledWith(
    "w",
    {
      expected_budget_revision: 7,
      addition: { model_calls: 2, total_tokens: 0, active_seconds: 0 },
      reason: "读完剩余材料",
    },
    expect.any(String),
  );
});
it("clears prior private content when a refresh reports revoked access", async () => {
  const client = api({
    messages: vi.fn().mockResolvedValue({
      items: [
        {
          entry_id: "m",
          seq: 1,
          kind: "assistant",
          body: "私有结果",
          visibility: "available",
        },
      ],
      next_after: 1,
    }),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="w"
      />,
    ),
  );
  expect(el.textContent).toContain("私有结果");
  client.work.mockRejectedValue(new HrLoopError(403, "dependency_revoked", {}));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 2600));
  });
  expect(el.textContent).not.toContain("私有结果");
  expect(el.textContent).toContain("当前账号");
  expect(button("发送").disabled).toBe(true);
});

it("clears inherited objects when starting an unrelated new work", async () => {
  const client = api({
    input: vi.fn().mockResolvedValue({
      input_revision: 2,
      text: "old",
      objects: [{ kind: "candidate", id: "candidate-a" }],
      references: [],
    }),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="w"
      />,
    ),
  );
  await act(async () => button("新工作").click());
  await type("新问题");
  await act(async () => button("发送").click());
  expect(client.submit.mock.calls[0][0].objects).toEqual([]);
});

it("keeps a restored work inaccessible if its first read fails", async () => {
  const client = api({
    work: vi
      .fn()
      .mockRejectedValue(new HrLoopError(403, "scope_forbidden", {})),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="w"
      />,
    ),
  );
  await type("补充");
  expect(button("发送").disabled).toBe(true);
  expect(client.submit).not.toHaveBeenCalled();
});

it("uploads before parsing and clearly marks partial PDF coverage", async () => {
  vi.mocked(beginAttachmentUpload).mockResolvedValue({
    uploadId: "upload",
    attachmentId: "attachment",
  } as never);
  vi.mocked(uploadAttachmentContent).mockResolvedValue({} as never);
  vi.mocked(completeAttachmentUpload).mockResolvedValue({} as never);
  const parse = vi.fn().mockResolvedValue({ state: "queued" });
  const textRef = { ...ref, kind: "material_text" };
  const material = vi
    .fn()
    .mockResolvedValueOnce({
      attachment_id: "attachment",
      state: "ready",
      parse_state: "not_started",
      text_ref: null,
      original_ref: ref,
      coverage_complete: false,
    })
    .mockResolvedValue({
      attachment_id: "attachment",
      state: "ready",
      parse_state: "ready",
      text_ref: textRef,
      original_ref: ref,
      coverage_complete: false,
      coverage_notes: ["部分页面含扫描图片"],
    });
  const client = api({ material, parse });
  await act(async () =>
    root.render(<HrLoopWorkspace account={account} api={client as never} />),
  );
  const file = new File(["pdf"], "公开JD.pdf", { type: "application/pdf" });
  await act(async () => {
    const input = el.querySelector("input[type=file]")!;
    Object.defineProperty(input, "files", { value: [file] });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(beginAttachmentUpload).toHaveBeenCalledWith(null, file, "csrf");
  expect(parse).toHaveBeenCalledWith("attachment", expect.any(String));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 1600));
  });
  expect(el.textContent).toContain("正文部分可读");
  expect(el.textContent).toContain("部分页面含扫描图片");
  await type("根据已读部分讨论");
  await act(async () => button("发送").click());
  expect(client.submit.mock.calls[0][0].references).toEqual([textRef]);
});

it("does not restore a late position result after a work access revocation", async () => {
  let resolve!: (v: unknown) => void;
  const privateResult = {
    ref,
    kind: "research",
    title: "撤权资料",
    body: "不应恢复的私有内容",
    objects: [],
    changes: [],
    base_standard_ref: null,
    basis: [],
  };
  const client = api({
    work: vi
      .fn()
      .mockRejectedValue(new HrLoopError(403, "dependency_revoked", {})),
    results: vi.fn((query: { position?: string }) =>
      query.position
        ? new Promise((r) => {
            resolve = r;
          })
        : Promise.resolve({ items: [], next_cursor: null }),
    ),
    result: vi.fn().mockResolvedValue(privateResult),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        positionApi={
          {
            position: vi.fn().mockResolvedValue({
              positionId: "p",
              title: "岗位",
              locations: [],
            }),
          } as never
        }
        initialWorkId="w"
        initialPositionId="p"
      />,
    ),
  );
  await act(async () => resolve({ items: [{ ref }], next_cursor: null }));
  expect(el.textContent).not.toContain("不应恢复的私有内容");
});

it("restores references again after a failed refresh and same revision reload", async () => {
  const client = api({
    input: vi.fn().mockResolvedValue({
      input_revision: 2,
      text: "old",
      objects: [],
      references: [ref],
    }),
    work: vi.fn().mockResolvedValue(work),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="w"
      />,
    ),
  );
  expect(el.querySelector('[aria-label="本次参考"]')?.textContent).toContain(
    "已存成果",
  );
  client.work.mockRejectedValueOnce(
    new HrLoopError(503, "temporarily_unavailable", {}),
  );
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 2600));
  });
  await act(async () => button("重新加载").click());
  await type("继续");
  await act(async () => button("发送").click());
  expect(client.append.mock.calls[0][1].references).toEqual([ref]);
});

it("keeps the latest selected method when earlier reads arrive late", async () => {
  let resolve!: (v: unknown) => void;
  const a = { ...ref, kind: "method", id: "a" },
    b = { ...ref, kind: "method", id: "b" };
  const client = api({
    knowledge: vi.fn().mockResolvedValue({
      release_id: "release",
      items: [
        { ref: a, title: "方法 A", description: "" },
        { ref: b, title: "方法 B", description: "" },
      ],
    }),
    method: vi.fn((r: { id: string }) =>
      r.id === "a"
        ? new Promise((done) => {
            resolve = done;
          })
        : Promise.resolve({ ref: b, text: "B 正文" }),
    ),
  });
  await act(async () =>
    root.render(<HrLoopWorkspace account={account} api={client as never} />),
  );
  await act(async () => button("方法 A").click());
  await act(async () => button("方法 B").click());
  await act(async () => resolve({ ref: a, text: "A 迟到正文" }));
  expect(el.querySelector(".hr-loop-method")?.textContent).toContain("B 正文");
  expect(el.querySelector(".hr-loop-method")?.textContent).not.toContain(
    "A 迟到正文",
  );
});
it("shows the exact original standard text for destructive changes", async () => {
  const standardRef = { ...ref, kind: "standard", id: "s" };
  const proposal = {
    ref,
    kind: "standard_proposal",
    title: "调整建议",
    body: "说明",
    objects: [],
    changes: [
      {
        change_id: "remove",
        action: "remove",
        target_item_id: "old-item",
        text: null,
      },
    ],
    base_standard_ref: standardRef,
    basis: [],
  };
  const client = api({
    work: vi.fn().mockResolvedValue({ ...work, result_refs: [ref] }),
    result: vi.fn().mockResolvedValue(proposal),
    standard: vi.fn().mockResolvedValue({
      ref: standardRef,
      items: [{ item_id: "old-item", text: "原有五年经验要求" }],
      confirmed_at: "today",
    }),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        positionApi={
          {
            position: vi.fn().mockResolvedValue({
              positionId: "p",
              title: "岗位",
              locations: [],
            }),
          } as never
        }
        initialWorkId="w"
        initialPositionId="p"
      />,
    ),
  );
  expect(el.querySelector(".hr-loop-result fieldset")?.textContent).toContain(
    "原有五年经验要求",
  );
});

it("keeps the original budget revision and retry identity if another client updates it", async () => {
  const extend = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("network"))
    .mockResolvedValue({ ...work, state: "queued" });
  const client = api({
    work: vi
      .fn()
      .mockResolvedValueOnce({
        ...work,
        state: "waiting_budget",
        budget: { ...work.budget, revision: 7 },
      })
      .mockResolvedValue({
        ...work,
        state: "waiting_budget",
        budget: { ...work.budget, revision: 8 },
      }),
    extend,
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="w"
      />,
    ),
  );
  await act(async () => {
    const n = el.querySelector("input[type=number]")!;
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(n, "2");
    n.dispatchEvent(new Event("input", { bubbles: true }));
    const r = el.querySelector(".hr-loop-budget input:not([type=number])")!;
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(r, "继续");
    r.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("按以上额度继续").click());
  await act(async () => {
    await new Promise((r) => setTimeout(r, 2600));
  });
  await act(async () => button("按以上额度继续").click());
  expect(extend.mock.calls[1][1].expected_budget_revision).toBe(7);
  expect(extend.mock.calls[1][2]).toBe(extend.mock.calls[0][2]);
});
it("shows rejected uploads as unavailable instead of still checking", async () => {
  vi.mocked(beginAttachmentUpload).mockResolvedValue({
    uploadId: "upload",
    attachmentId: "attachment",
  } as never);
  vi.mocked(uploadAttachmentContent).mockResolvedValue({} as never);
  vi.mocked(completeAttachmentUpload).mockResolvedValue({} as never);
  const client = api({
    material: vi.fn().mockResolvedValue({
      attachment_id: "attachment",
      state: "rejected",
      parse_state: "not_started",
      text_ref: null,
      original_ref: null,
      coverage_complete: false,
    }),
  });
  await act(async () =>
    root.render(<HrLoopWorkspace account={account} api={client as never} />),
  );
  await act(async () => {
    const input = el.querySelector("input[type=file]")!;
    Object.defineProperty(input, "files", {
      value: [new File(["bad"], "JD.pdf", { type: "application/pdf" })],
    });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(el.querySelector(".hr-loop-upload")?.textContent).toContain(
    "文件未通过检查",
  );
  expect(el.querySelector(".hr-loop-upload")?.textContent).not.toContain(
    "正在检查",
  );
});

it.each([
  ["user_temporary", null, 2, "临时要求", "第 2 次已接收输入"],
  [
    "confirmed_standard",
    { ...ref, kind: "standard" },
    null,
    "已确认标准",
    "成果保存时引用的标准版本",
  ],
  [
    "official_original",
    { ...ref, kind: "material", id: "source:original" },
    null,
    "官网原文",
    "成果保存时引用的官网材料",
  ],
])(
  "renders verified %s basis independently of prose",
  async (kind, source, inputRevision, label, meaning) => {
    const result = {
      ref,
      kind: "requirements",
      title: "要求分析",
      body: "正文没有基准标签。",
      objects: [],
      changes: [],
      base_standard_ref: null,
      basis: [{ kind, ref: source, input_revision: inputRevision }],
    };
    const client = api({
      work: vi.fn().mockResolvedValue({ ...work, result_refs: [ref] }),
      result: vi.fn().mockResolvedValue(result),
    });
    await act(async () =>
      root.render(
        <HrLoopWorkspace
          account={account}
          api={client as never}
          initialWorkId="w"
        />,
      ),
    );
    const basis = el.querySelector('[aria-label="基准性质"]');
    expect(basis?.textContent).toContain(label);
    expect(basis?.textContent).toContain(meaning);
    expect(basis?.textContent).not.toContain(String(kind));
    expect(basis?.textContent).not.toContain("v1");
  },
);
it.each([403, 410])(
  "clears private navigation and draft on revocation %s despite delayed success",
  async (status) => {
    let reject!: (v: unknown) => void;
    let resolve!: (v: unknown) => void;
    const client = api({
      threads: vi
        .fn()
        .mockResolvedValue({
          items: [{ thread_id: "private-thread", title: "私有历史标题" }],
          next_cursor: null,
        }),
      works: vi.fn().mockResolvedValue({ items: [work], next_cursor: null }),
      work: vi.fn(
        () =>
          new Promise((_r, j) => {
            reject = j;
          }),
      ),
      results: vi.fn((q: { position?: string }) =>
        q.position
          ? new Promise((r) => {
              resolve = r;
            })
          : Promise.resolve({ items: [], next_cursor: null }),
      ),
      result: vi
        .fn()
        .mockResolvedValue({
          ref,
          kind: "research",
          title: "迟到私有成果",
          body: "迟到私有正文",
          objects: [],
          changes: [],
          base_standard_ref: null,
          basis: [],
        }),
    });
    await act(async () =>
      root.render(
        <HrLoopWorkspace
          account={account}
          api={client as never}
          positionApi={
            {
              position: vi
                .fn()
                .mockResolvedValue({
                  positionId: "p",
                  title: "私有岗位名称",
                  locations: [],
                }),
            } as never
          }
          initialWorkId="w"
          initialPositionId="p"
        />,
      ),
    );
    await act(async () => button("私有历史标题").click());
    await type("尚未发送的私有草稿");
    expect(el.textContent).toContain("私有历史标题");
    expect(el.textContent).toContain("私有岗位名称");
    expect(el.textContent).toContain("等待你的补充 · 第 2 次输入");
    await act(async () =>
      reject(new HrLoopError(status, "dependency_revoked", {})),
    );
    await act(async () => resolve({ items: [{ ref }], next_cursor: null }));
    expect(el.textContent).not.toContain("私有历史标题");
    expect(el.textContent).not.toContain("私有岗位名称");
    expect(el.textContent).not.toContain("等待你的补充 · 第 2 次输入");
    expect(el.textContent).not.toContain("迟到私有");
    expect(el.querySelector("textarea")?.value).toBe("");
  },
);
it("previews method prose without YAML or broken relative source navigation", async () => {
  const methodRef = { ...ref, kind: "method" };
  const source =
    "---\nid: private-method-id\nrevision: hidden-revision\n---\n# 方法正文\n[内部来源](../sources/method.md) 与 [公开来源](https://example.org/source)";
  const client = api({
    knowledge: vi
      .fn()
      .mockResolvedValue({
        release_id: "release",
        items: [{ ref: methodRef, title: "阅读方法", description: "" }],
      }),
    method: vi.fn().mockResolvedValue({ ref: methodRef, text: source }),
  });
  await act(async () =>
    root.render(<HrLoopWorkspace account={account} api={client as never} />),
  );
  await act(async () => button("阅读方法").click());
  const preview = el.querySelector(".hr-loop-method")!;
  expect(preview.textContent).toContain("方法正文");
  expect(preview.textContent).toContain("内部来源");
  expect(preview.textContent).not.toContain("private-method-id");
  expect(preview.querySelector('a[href="../sources/method.md"]')).toBeNull();
  expect(
    preview.querySelector('a[href="https://example.org/source"]'),
  ).not.toBeNull();
  await act(async () => button("带此方法讨论").click());
  await type("讨论");
  await act(async () => button("发送").click());
  expect(client.submit.mock.calls[0][0].references).toEqual([methodRef]);
});
it("labels real parsed material refs and can add the exact current standard", async () => {
  const standardRef = { ...ref, kind: "standard", id: "standard" };
  const client = api({
    input: vi
      .fn()
      .mockResolvedValue({
        input_revision: 2,
        text: "old",
        objects: [],
        references: [{ ...ref, kind: "material", id: "attachment:text" }],
      }),
    standard: vi
      .fn()
      .mockResolvedValue({
        ref: standardRef,
        items: [],
        confirmed_at: "today",
      }),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        positionApi={
          {
            position: vi
              .fn()
              .mockResolvedValue({
                positionId: "p",
                title: "岗位",
                locations: [],
              }),
          } as never
        }
        initialPositionId="p"
        initialWorkId="w"
      />,
    ),
  );
  expect(el.querySelector('[aria-label="本次参考"]')?.textContent).toContain(
    "材料正文",
  );
  await act(async () => button("带此标准讨论").click());
  await type("继续");
  await act(async () => button("发送").click());
  expect(client.append.mock.calls[0][1].references).toContainEqual(standardRef);
});

it("selects a published intelligence body into the submitted input", async () => {
  const intelligenceRef = { ...ref, kind: "intelligence" };
  const client = api({
    knowledge: vi
      .fn()
      .mockImplementation(async (kind?: string) => ({
        release_id: "publication",
        items:
          kind === "intelligence"
            ? [
                {
                  ref: intelligenceRef,
                  title: "公司研究",
                  description: "公开资料",
                  objects: [{ kind: "company", id: "example" }],
                },
              ]
            : [],
      })),
    intelligence: vi
      .fn()
      .mockResolvedValue({
        ref: intelligenceRef,
        text: "---\nscope: company\nobserved_at: 2026-09-06T08:00:00+00:00\n---\n# 公司研究\n公开证据与未知项。",
      }),
  });
  await act(async () =>
    root.render(<HrLoopWorkspace account={account} api={client as never} />),
  );
  await act(async () => button("公司与专题情报").click());
  await act(async () => button("阅读：公司研究").click());
  await act(async () => button("带此情报讨论").click());
  await type("结合这份研究讨论");
  await act(async () => button("发送").click());
  expect(client.submit.mock.calls[0][0].references).toEqual([intelligenceRef]);
});

it("reopens an old restored intelligence ref and retains it when unavailable", async () => {
  const old = { ...ref, kind: "intelligence", revision: "b1" };
  const latest = { ...old, revision: "b2", sha256: "b" };
  const client = api({
    input: vi
      .fn()
      .mockResolvedValue({
        input_revision: 2,
        text: "old",
        objects: [],
        references: [old],
      }),
    knowledge: vi
      .fn()
      .mockImplementation(async (kind?: string) => ({
        release_id: "publication",
        items:
          kind === "intelligence"
            ? [{ ref: latest, title: "新研究", description: "" }]
            : [],
      })),
    intelligence: vi
      .fn()
      .mockRejectedValue(new HrLoopError(410, "reference_unavailable", {})),
  });
  await act(async () =>
    root.render(
      <HrLoopWorkspace
        account={account}
        api={client as never}
        initialWorkId="w"
      />,
    ),
  );
  await type("未提交的问题");
  await act(async () => button("已选情报 1").click());
  expect(client.intelligence).toHaveBeenCalledWith(old);
  expect(el.textContent).toContain("这份情报已不可用");
  expect(el.querySelector("textarea")?.value).toBe("未提交的问题");
  expect(button("已选情报 1")).toBeDefined();
  expect(button("发送").disabled).toBe(true);
  expect(client.intelligence).not.toHaveBeenCalledWith(latest);
  await act(async () =>
    el.querySelector<HTMLButtonElement>('[aria-label="移除参考 1"]')!.click(),
  );
  expect(el.querySelector('[aria-label="本次参考"]')).toBeNull();
  expect(button("发送").disabled).toBe(false);
});

it("opens the batch material panel lazily and clears it when starting a new conversation", async () => {
  const candidates = {batches: vi.fn().mockResolvedValue({items: []}), candidates: vi.fn().mockResolvedValue({items: []})};
  await act(async () => root.render(<HrLoopWorkspace account={account} api={api() as never} candidatesApi={candidates as never} />));
  expect(candidates.batches).not.toHaveBeenCalled();
  expect(button("批量简历材料")).toBeDefined();
  await act(async () => button("批量简历材料").click());
  expect(candidates.batches).toHaveBeenCalledOnce();
  expect(el.querySelector('section[aria-label="批量简历材料"]')).not.toBeNull();
  await act(async () => button("新工作").click());
  expect(el.querySelector('section[aria-label="批量简历材料"]')).toBeNull();
});
