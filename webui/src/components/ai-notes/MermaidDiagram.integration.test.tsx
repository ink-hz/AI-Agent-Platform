/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mermaidImageSource } from "./MermaidDiagram";


const CONTENT_ROOT = resolve(process.cwd(), "../backend/app/ai_notes/content");
const SEMANTIC_FILLS = [
  "#DBEAFE", "#EDE9FE", "#CCFBF1", "#FEF3C7",
  "#DCFCE7", "#D1FAE5", "#FEE2E2", "#F3F4F6",
];
let productionDiagramSequence = 0;


function productionArticle(relativePath: string): string {
  return readFileSync(resolve(CONTENT_ROOT, relativePath), "utf8");
}


function mermaidBlocks(markdown: string): string[] {
  return [...markdown.matchAll(/```mermaid\n([\s\S]*?)\n```/g)]
    .map((match) => match[1] ?? "");
}


function expectSemanticStyling(source: string): void {
  expect(source).toMatch(/\b(?:classDef|style)\b/);
  expect(SEMANTIC_FILLS.some((color) => source.includes(color))).toBe(true);
}


async function expectProductionDiagramsToRender(sources: string[]): Promise<void> {
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "neutral",
    htmlLabels: false,
    flowchart: { htmlLabels: false },
  });
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

  it("keeps flowchart labels in a sanitized CSP-compatible image", async () => {
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      htmlLabels: false,
      flowchart: { htmlLabels: false },
    });

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

  it("renders the styled Agent engineering learning map", async () => {
    const markdown = productionArticle(
      "01-foundations/01-agent-engineering-learning-map.md",
    );
    const sources = mermaidBlocks(markdown);
    expect(sources).toHaveLength(2);
    expect(sources.join("\n")).toContain("最小行动循环");
    expect(sources.join("\n")).toContain("能力递进");
    await expectProductionDiagramsToRender(sources);
  });
});
