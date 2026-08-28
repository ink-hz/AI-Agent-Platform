/** @vitest-environment jsdom */

import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AI_NOTES_MERMAID_CONFIG, mermaidImageSource } from "./MermaidDiagram";
import { mermaidMetadata } from "./mermaidMetadata";


const CONTENT_ROOT = resolve(process.cwd(), "../backend/app/ai_notes/content");
const SEMANTIC_FILLS = [
  "#DBEAFE", "#EDE9FE", "#CCFBF1", "#FEF3C7",
  "#DCFCE7", "#D1FAE5", "#FEE2E2", "#F3F4F6",
];
let productionDiagramSequence = 0;


function frontmatter(source: string): string {
  const matched = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!matched) throw new Error("invalid AI note frontmatter");
  return matched[1] ?? "";
}


function publishedArticleFiles(contentRoot = CONTENT_ROOT): string[] {
  return readdirSync(contentRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((category) => {
      const categoryPath = join(contentRoot, category.name);
      return readdirSync(categoryPath, { withFileTypes: true })
        .filter((entry) => (
          entry.isFile()
          && entry.name !== "_index.md"
          && entry.name.endsWith(".md")
        ))
        .sort((left, right) => left.name.localeCompare(right.name))
        .map((entry) => join(categoryPath, entry.name));
    })
    .filter((path) => (
      /^draft:\s*false\s*$/m.test(frontmatter(readFileSync(path, "utf8")))
    ));
}


function mermaidBlocks(markdown: string): string[] {
  return [...markdown.matchAll(/```mermaid\n([\s\S]*?)\n```/g)]
    .map((match) => match[1] ?? "");
}


function expectSemanticStyling(source: string): void {
  expect(source).toMatch(/\b(?:classDef|style)\b/);
  expect(SEMANTIC_FILLS.some((color) => source.includes(color))).toBe(true);
}


function expectAccessibilityMetadata(sources: string[]): void {
  for (const source of sources) {
    expect(source).toMatch(/^\s*accTitle:\s*\S.+$/m);
    expect(source).toMatch(/^\s*accDescr:\s*\S.+$/m);
  }
}


async function expectProductionDiagramsToRender(sources: string[]): Promise<void> {
  expectAccessibilityMetadata(sources);
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize(AI_NOTES_MERMAID_CONFIG);
  for (const source of sources) {
    expectSemanticStyling(source);
    const rendered = await mermaid.render(
      `ai-note-production-${++productionDiagramSequence}`,
      source,
    );
    const imageSource = mermaidImageSource(rendered.svg);
    const sanitized = decodeURIComponent(imageSource.split(",", 2)[1] ?? "");
    expect(imageSource).toMatch(/^data:image\/svg\+xml;charset=utf-8,/);
    expect(sanitized).toContain("<svg");
    expect(sanitized).not.toContain("<script");
    expect(sanitized).not.toContain("<foreignObject");
    expect(SEMANTIC_FILLS.some((color) => sanitized.includes(color))).toBe(true);
  }
}


class TestStyleSheet {
  cssRules: Array<{ cssText: string }> = [];

  insertRule(cssText: string, position = this.cssRules.length): number {
    this.cssRules.splice(position, 0, { cssText });
    return position;
  }
}


describe("MermaidDiagram with the real renderer", () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, "CSSStyleSheet", {
      configurable: true,
      value: TestStyleSheet,
    });
    Object.defineProperty(SVGElement.prototype, "getBBox", {
      configurable: true,
      value: () => ({ x: 0, y: 0, width: 100, height: 20 }),
    });
    Object.defineProperty(SVGElement.prototype, "getComputedTextLength", {
      configurable: true,
      value: () => 80,
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(SVGElement.prototype, "getBBox");
    Reflect.deleteProperty(SVGElement.prototype, "getComputedTextLength");
  });

  it("discovers published articles without a registration list", () => {
    const root = mkdtempSync(join(tmpdir(), "ai-notes-discovery-"));
    try {
      const category = join(root, "01-foundations");
      mkdirSync(category);
      writeFileSync(
        join(category, "_index.md"),
        "---\ntitle: 基础\nslug: foundations\n---\n",
      );
      writeFileSync(
        join(category, "01-live.md"),
        "---\ndraft: false\n---\n\n## 正文\n",
      );
      writeFileSync(
        join(category, "02-draft.md"),
        "---\ndraft: true\n---\n\n## 草稿\n",
      );

      expect(publishedArticleFiles(root)).toEqual([join(category, "01-live.md")]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("keeps flowchart labels in a sanitized CSP-compatible image", async () => {
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize(AI_NOTES_MERMAID_CONFIG);

    const { svg } = await mermaid.render(
      "ai-note-mermaid-integration",
      "graph TD; A[Start]-->B[End]",
    );
    const source = mermaidImageSource(svg);
    const rendered = decodeURIComponent(source.split(",", 2)[1] ?? "");

    expect(source).toMatch(/^data:image\/svg\+xml;charset=utf-8,/);
    expect(rendered).toContain("Start");
    expect(rendered).toContain("End");
    expect(rendered).not.toContain("foreignObject");
    expect(document.querySelector("style")).toBeNull();
  });

  it("renders unstyled groups on the white article canvas", async () => {
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize(AI_NOTES_MERMAID_CONFIG);

    const { svg } = await mermaid.render(
      "ai-note-white-group-integration",
      `flowchart TB
        subgraph GROUP[系统边界]
          A[输入] --> B[输出]
        end`,
    );
    const source = mermaidImageSource(svg);
    const rendered = decodeURIComponent(source.split(",", 2)[1] ?? "");

    expect(rendered).toMatch(/\.cluster rect\s*\{[^}]*fill:\s*#FFFFFF;/s);
    expect(rendered).toMatch(/\.cluster rect\s*\{[^}]*stroke:\s*#CBD5E1;/s);
  });

  it("renders every published Mermaid diagram with unique metadata", async () => {
    const files = publishedArticleFiles();
    const sources = files.flatMap((path) => mermaidBlocks(readFileSync(path, "utf8")));
    expect(files).toHaveLength(14);
    expect(sources).toHaveLength(43);
    await expectProductionDiagramsToRender(sources);

    const metadata = sources.map(mermaidMetadata);
    expect(metadata.every(({ description }) => Boolean(description))).toBe(true);
    expect(new Set(metadata.map(({ title }) => title)).size).toBe(sources.length);
    expect(new Set(metadata.map(({ description }) => description)).size).toBe(
      sources.length,
    );
  });

});
