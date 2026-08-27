/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mermaidImageSource } from "./MermaidDiagram";


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
});
