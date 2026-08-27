/** @vitest-environment jsdom */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AiNoteArticle as AiNoteArticleData } from "../../aiNotesTypes";
import { AiNoteArticle } from "./AiNoteArticle";
import { ArticleMarkdown } from "./ArticleMarkdown";


describe("ArticleMarkdown", () => {
  it("renders GFM, stable heading anchors, highlighted code, and safe links", () => {
    const html = renderToStaticMarkup(<ArticleMarkdown markdown={
      "## 标题\n\n## 标题\n\n> 引用\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```ts\nconst n = 1\n```\n\n[外部](https://example.com)"
    } />);

    expect(html).toContain('id="标题"');
    expect(html).toContain('id="标题-2"');
    expect(html).toContain("<blockquote>");
    expect(html).toContain('class="article-table-scroll"');
    expect(html).toContain('class="hljs language-ts"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("does not render raw HTML or dangerous protocols", () => {
    const html = renderToStaticMarkup(<ArticleMarkdown markdown={
      '<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n[y](data:text/html,bad)'
    } />);

    expect(html).not.toContain("<script>");
    expect(html).not.toContain('href="javascript:');
    expect(html).not.toContain('href="data:');
    expect(html).toContain("&lt;script&gt;");
  });

  it("renders restrained article metadata and the Markdown body", () => {
    const article: AiNoteArticleData = {
      slug: "handbook", title: "Agent 系统手册", filename: "handbook.md",
      description: "不作为顶部营销文案展示", published_at: "2026-08-27",
      updated_at: "2026-08-28", tags: ["Agent", "架构"], reading_minutes: 8,
      category_slug: "foundations", category_title: "基础与原理", markdown: "# 正文",
    };

    const html = renderToStaticMarkup(<AiNoteArticle article={article} />);

    expect(html).toContain("基础与原理 / handbook.md");
    expect(html).toContain("Agent 系统手册");
    expect(html).toContain('dateTime="2026-08-27"');
    expect(html).toContain("约 8 分钟");
    expect(html).toContain("Agent");
    expect(html).not.toContain("不作为顶部营销文案展示");
    expect(html).toContain('<h1 id="正文">正文</h1>');
  });
});
