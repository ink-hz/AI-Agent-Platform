/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrKnowledgePanel } from "./HrKnowledgePanel";

const resource = { id: "structured-interview", title: "结构化面试", revision: 1,
  domains: ["招聘"], knowledgeForms: ["方法"], path: "recruiting/structured-interview.md", sha256: "a".repeat(64) };

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

it("browses metadata and markdown, then explicitly carries the method into discussion", async () => {
  const onSelect = vi.fn();
  const client = { index: vi.fn().mockResolvedValue({ sourceCommit: "abc123", index: "# 方法索引", resources: [resource] }),
    article: vi.fn().mockResolvedValue({ ...resource, sourceCommit: "abc123", markdown: "# 结构化面试\n\n## 来源\n\n研究来源" }) };
  await act(async () => root.render(<HrKnowledgePanel client={client} onClose={vi.fn()} onSelect={onSelect} />));
  expect(container.textContent).toContain("招聘");
  expect(container.textContent).toContain("方法");
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("结构化面试"))!.click());
  expect(container.textContent).toContain("研究来源");
  expect(container.textContent).toContain("版本 1");
  expect(onSelect).not.toHaveBeenCalled();
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "带着这个方法讨论")!.click());
  expect(onSelect).toHaveBeenCalledWith({ sourceCommit: "abc123", id: resource.id, revision: resource.revision, sha256: resource.sha256 });
});

it("keeps the latest selected article when an earlier request resolves last", async () => {
  const first = { ...resource, id: "first", title: "第一个方法" };
  const second = { ...resource, id: "second", title: "第二个方法" };
  const firstResponse = deferred<never>();
  const client = {
    index: vi.fn().mockResolvedValue({ sourceCommit: "abc123", index: "[错误的相对链接](recruiting/first.md)", resources: [first, second] }),
    article: vi.fn().mockImplementation((_commit, id) => id === "first" ? firstResponse.promise : Promise.resolve({ ...second, sourceCommit: "abc123", markdown: "# 第二个方法正文\n\n[其他领域](../other/model.md) [外部来源](https://example.com/source)" })),
  };
  await act(async () => root.render(<HrKnowledgePanel client={client} onClose={vi.fn()} onSelect={vi.fn()} />));
  const button = (label: string) => [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label))!;
  await act(async () => { button("第一个方法").click(); button("第二个方法").click(); });
  expect(container.textContent).toContain("第二个方法正文");
  await act(async () => firstResponse.resolve({ ...first, sourceCommit: "abc123", markdown: "# 过期正文" } as never));
  expect(container.textContent).toContain("第二个方法正文");
  expect(container.textContent).not.toContain("过期正文");
  expect(container.querySelector('a[href="recruiting/first.md"]')).toBeNull();
  expect(container.querySelector('a[href="../other/model.md"]')).toBeNull();
  expect(container.querySelector('a[href="https://example.com/source"]')).not.toBeNull();
});
