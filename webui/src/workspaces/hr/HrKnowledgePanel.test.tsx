/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrKnowledgePanel } from "./HrKnowledgePanel";

const resource = { id: "structured-interview", title: "结构化面试", revision: "2026-09-08",
  domains: ["招聘"], knowledgeForms: ["方法"], path: "recruiting/structured-interview.md", sha256: "a".repeat(64) };

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("browses metadata and markdown, then explicitly carries the method into discussion", async () => {
  const onSelect = vi.fn();
  const client = { index: vi.fn().mockResolvedValue({ sourceCommit: "abc123", index: "# 方法索引", resources: [resource] }),
    article: vi.fn().mockResolvedValue({ ...resource, sourceCommit: "abc123", markdown: "# 结构化面试\n\n## 来源\n\n研究来源" }) };
  await act(async () => root.render(<HrKnowledgePanel client={client} onClose={vi.fn()} onSelect={onSelect} />));
  expect(container.textContent).toContain("招聘");
  expect(container.textContent).toContain("方法");
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("结构化面试"))!.click());
  expect(container.textContent).toContain("研究来源");
  expect(container.textContent).toContain("版本 2026-09-08");
  expect(onSelect).not.toHaveBeenCalled();
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "带着这个方法讨论")!.click());
  expect(onSelect).toHaveBeenCalledWith({ sourceCommit: "abc123", id: resource.id, revision: resource.revision, sha256: resource.sha256 });
});
