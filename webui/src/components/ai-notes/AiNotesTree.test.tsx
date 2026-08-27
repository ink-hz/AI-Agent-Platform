/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AiNotesIndex } from "../../aiNotesTypes";
import { AiNotesTree } from "./AiNotesTree";


const index: AiNotesIndex = {
  categories: [
    {
      slug: "foundations",
      title: "基础与原理",
      articles: [
        {
          slug: "handbook", title: "Agent 系统手册", filename: "handbook.md",
          description: "说明", author: "苍渊", motto: "博观而约取，厚积而薄发。",
          published_at: "2026-08-27", updated_at: null,
          tags: ["Agent"], reading_minutes: 8,
        },
      ],
    },
    {
      slug: "tools",
      title: "工具与框架",
      articles: [
        {
          slug: "frameworks", title: "框架选型", filename: "framework-guide.md",
          description: "说明", author: "苍渊", motto: null,
          published_at: "2026-08-27", updated_at: null,
          tags: [], reading_minutes: 3,
        },
      ],
    },
  ],
};


describe("AiNotesTree", () => {
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

  it("shows counts, expands the selection, and changes articles explicitly", async () => {
    const onSelect = vi.fn();
    await act(async () => root.render(
      <AiNotesTree
        index={index}
        selectedPath={{ categorySlug: "foundations", articleSlug: "handbook" }}
        onSelect={onSelect}
      />,
    ));

    expect(container.textContent).toContain("2 篇文章");
    expect(container.textContent).toContain("基础与原理1");
    expect(container.querySelector('[aria-current="page"]')?.textContent).toContain("Agent 系统手册");
    const toolsToggle = container.querySelector<HTMLButtonElement>('button[data-category="tools"]')!;
    expect(toolsToggle.getAttribute("aria-expanded")).toBe("false");
    await act(async () => toolsToggle.click());
    await act(async () => container.querySelector<HTMLButtonElement>('button[data-article="tools/frameworks"]')!.click());
    expect(onSelect).toHaveBeenCalledWith("tools", "frameworks");
  });

  it("searches titles, filenames, and category names without changing selection", async () => {
    const onSelect = vi.fn();
    await act(async () => root.render(
      <AiNotesTree index={index} selectedPath={null} onSelect={onSelect} />,
    ));
    const search = container.querySelector<HTMLInputElement>('input[aria-label="搜索文章"]')!;

    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "framework-guide");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.textContent).toContain("框架选型");
    expect(container.textContent).not.toContain("Agent 系统手册");

    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "基础与原理");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.textContent).toContain("Agent 系统手册");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("shows a no-results state without clearing the reader", async () => {
    const onSelect = vi.fn();
    await act(async () => root.render(
      <AiNotesTree index={index} selectedPath={null} onSelect={onSelect} />,
    ));
    const search = container.querySelector<HTMLInputElement>('input[aria-label="搜索文章"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "不存在");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(container.textContent).toContain("没有匹配的文章");
    expect(onSelect).not.toHaveBeenCalled();
  });
});
