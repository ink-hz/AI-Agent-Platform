/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { HrLoopError } from "../../hrLoopApi";
import { HrLoopIntelligencePicker } from "./HrLoopIntelligencePicker";

const first = {
  kind: "intelligence",
  id: "company:example",
  revision: "b1",
  sha256: "a".repeat(64),
};
const second = { ...first, revision: "b2", sha256: "b".repeat(64) };
const item = (ref = first) => ({
  ref,
  title: "示例公司研究",
  description: "公开公司与岗位资料",
  objects: [{ kind: "company", id: "example" }],
});
const prose = (date = "2026-09-06") =>
  `---\nbundle_id: hidden-bundle-id\nobserved_at: ${date}T08:00:00+00:00\nscope: company\ncoverage: partial\n---\n# 示例公司研究\n\n具体研究判断。[证据](https://example.com/source)\n\n未知：实际编制。`;
let root: ReturnType<typeof createRoot>;
let el: HTMLDivElement;
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
function setup() {
  return {
    knowledge: vi.fn().mockResolvedValue({ items: [item()], release_id: "r1" }),
    intelligence: vi.fn().mockResolvedValue({ ref: first, text: prose() }),
  };
}
async function render(api: ReturnType<typeof setup>, extras = {}) {
  const onSelect = vi.fn(),
    onAccessError = vi.fn();
  await act(async () =>
    root.render(
      <HrLoopIntelligencePicker
        api={api as never}
        disabled={false}
        onSelect={onSelect}
        onClose={() => {}}
        onAccessError={onAccessError}
        {...extras}
      />,
    ),
  );
  return { onSelect, onAccessError };
}
it("shows exact source scope/time and preserves selected B1 after refreshing B2 catalog", async () => {
  const api = setup();
  const { onSelect } = await render(api);
  expect(api.knowledge).toHaveBeenCalledWith("intelligence");
  await act(async () => button("阅读：示例公司研究").click());
  expect(el.textContent).toContain("公司研究");
  expect(el.querySelector("time")?.getAttribute("datetime")).toBe(
    "2026-09-06T08:00:00+00:00",
  );
  expect(el.textContent).toContain("部分覆盖");
  expect(el.textContent).toContain("未知：实际编制");
  expect(el.textContent).not.toContain("hidden-bundle-id");
  api.knowledge.mockResolvedValue({ items: [item(second)], release_id: "r2" });
  await act(async () => button("重新加载目录").click());
  await act(async () => button("带此情报讨论").click());
  expect(onSelect).toHaveBeenCalledWith(first);
  expect(api.intelligence).toHaveBeenCalledTimes(1);
});
it.each([410, 503])(
  "keeps an explicit old selection unavailable without fetching current on %s",
  async (status) => {
    const api = setup();
    api.knowledge.mockResolvedValue({
      items: [item(second)],
      release_id: "r2",
    });
    api.intelligence.mockRejectedValue(
      new HrLoopError(status, "reference_unavailable", {}),
    );
    const { onSelect, onAccessError } = await render(api, {
      initialRef: first,
    });
    expect(api.intelligence).toHaveBeenCalledWith(first);
    expect(api.intelligence).not.toHaveBeenCalledWith(second);
    expect(el.querySelector('[role="alert"]')?.textContent).toMatch(
      /不可用|不可读/,
    );
    expect(button("带此情报讨论")).toBeUndefined();
    expect(onSelect).not.toHaveBeenCalled();
    expect(onAccessError).not.toHaveBeenCalled();
  },
);
it("rejects a substituted response reference and reports real access revocation", async () => {
  const api = setup();
  api.intelligence.mockResolvedValue({
    ref: second,
    text: prose("2026-09-11"),
  });
  const { onSelect, onAccessError } = await render(api, { initialRef: first });
  expect(el.querySelector('[role="alert"]')).not.toBeNull();
  expect(onSelect).not.toHaveBeenCalled();
  api.intelligence.mockRejectedValue(new HrLoopError(403, "scope_denied", {}));
  await act(async () => button("重试读取").click());
  expect(onAccessError).toHaveBeenCalledWith(
    expect.objectContaining({ status: 403 }),
  );
});

it("ignores a late old body after explicitly opening another report", async () => {
  const api = setup();
  let resolve!: (value: { ref: typeof first; text: string }) => void;
  api.intelligence.mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  api.intelligence.mockResolvedValue({
    ref: second,
    text: prose("2026-09-11"),
  });
  api.knowledge.mockResolvedValue({ items: [item(second)], release_id: "r2" });
  const { onSelect } = await render(api, { initialRef: first });
  await act(async () => button("阅读：示例公司研究").click());
  await act(async () => resolve({ ref: first, text: prose() }));
  expect(el.querySelector("time")?.getAttribute("datetime")).toBe(
    "2026-09-11T08:00:00+00:00",
  );
  await act(async () => button("带此情报讨论").click());
  expect(onSelect).toHaveBeenCalledWith(second);
});
