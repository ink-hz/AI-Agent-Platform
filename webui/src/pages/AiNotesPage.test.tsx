/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AiNotesClient } from "../aiNotesApi";
import { AiNotesApiError, type AiNoteArticle, type AiNotesIndex } from "../aiNotesTypes";
import { AiNotesPage } from "./AiNotesPage";


const summary = {
  slug: "handbook", title: "Agent 系统手册", filename: "handbook.md",
  description: "说明", author: "苍渊", motto: "博观而约取，厚积而薄发。",
  published_at: "2026-08-27", updated_at: null,
  tags: ["Agent"], reading_minutes: 8,
};
const index: AiNotesIndex = {
  categories: [
    { slug: "foundations", title: "基础与原理", articles: [summary] },
    { slug: "tools", title: "工具与框架", articles: [{ ...summary, slug: "frameworks", title: "框架选型", filename: "frameworks.md" }] },
  ],
};
const article: AiNoteArticle = {
  ...summary,
  category_slug: "foundations",
  category_title: "基础与原理",
  markdown: "# 正文内容",
};
const emptyIndex: AiNotesIndex = {
  categories: [
    "基础与原理", "Agent 架构", "工具与框架", "AI 工程实践", "思考与方法",
  ].map((title, position) => ({ slug: `category-${position + 1}`, title, articles: [] })),
};


function clientWith(
  selectedIndex: AiNotesIndex,
  articleResult: AiNoteArticle | Error = article,
): AiNotesClient & { fetchIndex: ReturnType<typeof vi.fn>; fetchArticle: ReturnType<typeof vi.fn> } {
  return {
    fetchIndex: vi.fn().mockResolvedValue(selectedIndex),
    fetchArticle: articleResult instanceof Error
      ? vi.fn().mockRejectedValue(articleResult)
      : vi.fn().mockResolvedValue(articleResult),
  };
}


describe("AiNotesPage", () => {
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

  it("opens the first article and replaces the index URL", async () => {
    const client = clientWith(index);
    const onNavigate = vi.fn();
    await act(async () => root.render(<AiNotesPage client={client} onNavigate={onNavigate} />));

    expect(client.fetchArticle).toHaveBeenCalledWith("foundations", "handbook", expect.any(AbortSignal));
    expect(onNavigate).toHaveBeenCalledWith("/ai-notes/foundations/handbook", { replace: true });
    expect(container.textContent).toContain("Agent 系统手册");
  });

  it("opens the first article when navigation returns from a deep link to the index", async () => {
    const client = clientWith(index);
    const onNavigate = vi.fn();
    await act(async () => root.render(
      <AiNotesPage
        categorySlug="tools"
        articleSlug="frameworks"
        client={client}
        onNavigate={onNavigate}
      />,
    ));

    await act(async () => root.render(<AiNotesPage client={client} onNavigate={onNavigate} />));

    expect(onNavigate).toHaveBeenCalledWith("/ai-notes/foundations/handbook", { replace: true });
    expect(client.fetchArticle).toHaveBeenLastCalledWith(
      "foundations",
      "handbook",
      expect.any(AbortSignal),
    );
  });

  it("treats five empty categories as a valid empty state", async () => {
    await act(async () => root.render(<AiNotesPage client={clientWith(emptyIndex)} />));

    expect(container.textContent).toContain("暂无已发布文章");
    expect(container.textContent).toContain("基础与原理");
    expect(container.textContent).not.toContain("暂时不可用");
  });

  it("keeps the previous article when the next request fails", async () => {
    const client = clientWith(index);
    await act(async () => root.render(
      <AiNotesPage categorySlug="foundations" articleSlug="handbook" client={client} />,
    ));
    client.fetchArticle.mockRejectedValueOnce(new AiNotesApiError(503));
    await act(async () => root.render(
      <AiNotesPage categorySlug="tools" articleSlug="frameworks" client={client} />,
    ));

    expect(container.textContent).toContain("Agent 系统手册");
    expect(container.textContent).toContain("文章暂时无法打开");
  });

  it("keeps the tree on a deep-link 404", async () => {
    const client = clientWith(index, new AiNotesApiError(404));
    await act(async () => root.render(
      <AiNotesPage categorySlug="foundations" articleSlug="missing" client={client} />,
    ));

    expect(container.textContent).toContain("基础与原理");
    expect(container.textContent).toContain("文章不存在");
  });

  it("distinguishes an unavailable index from an empty index", async () => {
    const client = clientWith(index);
    client.fetchIndex.mockRejectedValue(new AiNotesApiError(503));
    await act(async () => root.render(<AiNotesPage client={client} />));

    expect(container.textContent).toContain("AI 工程笔记暂时不可用");
    expect(container.textContent).not.toContain("暂无已发布文章");
  });

  it("opens and closes the mobile directory and restores focus", async () => {
    await act(async () => root.render(<AiNotesPage client={clientWith(index)} />));
    const opener = container.querySelector<HTMLButtonElement>('button[aria-label="打开文章目录"]')!;
    expect(opener.getAttribute("aria-expanded")).toBe("false");

    await act(async () => opener.click());
    expect(container.querySelector('[role="dialog"][aria-label="文章目录"]')).not.toBeNull();
    expect(document.activeElement).toBe(container.querySelector('button[aria-label="关闭文章目录"]'));

    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    expect(container.querySelector('[role="dialog"][aria-label="文章目录"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
