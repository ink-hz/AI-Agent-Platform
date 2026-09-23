/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { AI_NOTES_MERMAID_CONFIG, mermaidImageSource } from "./components/ai-notes/MermaidDiagram";
import { prepareDesignMarkdown } from "./agentDesignMarkdown";
it("renders every HR design diagram using the production strict renderer and resolves source anchors", async () => {
  Object.defineProperty(globalThis, "CSSStyleSheet", { configurable: true, value: class { cssRules: Array<{cssText: string}> = []; insertRule(cssText: string, index = this.cssRules.length) { this.cssRules.splice(index,0,{cssText}); return index; } } });
  Object.defineProperty(SVGElement.prototype, "getBBox", { configurable: true, value: () => ({ x:0,y:0,width:100,height:20 }) });
  Object.defineProperty(SVGElement.prototype, "getComputedTextLength", { configurable: true, value: () => 80 });
  try {
    const markdown = readFileSync(resolve(process.cwd(), "../backend/app/agent_designs/content/hr.md"), "utf8");
    const prepared = prepareDesignMarkdown(markdown);
    for (const match of markdown.matchAll(/\]\(#([^)]*)\)/g)) expect(Object.values(prepared.headingIds)).toContain(match[1]);
    const blocks = [...markdown.matchAll(/```mermaid\n([\s\S]*?)\n```/g)];
    expect(blocks.length).toBeGreaterThan(5);
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize(AI_NOTES_MERMAID_CONFIG);
    for (const [index, block] of blocks.entries()) {
      const output = await mermaid.render(`hr-design-check-${index}`, block[1]);
      expect(mermaidImageSource(output.svg)).toContain("data:image/svg+xml");
    }
  } finally {
    Reflect.deleteProperty(SVGElement.prototype, "getBBox");
    Reflect.deleteProperty(SVGElement.prototype, "getComputedTextLength");
  }
}, 30000);
