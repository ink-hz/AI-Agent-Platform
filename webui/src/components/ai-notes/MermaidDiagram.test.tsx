/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const initialize = vi.fn();
const render = vi.fn();
vi.mock("mermaid", () => ({ default: { initialize, render } }));

import { MermaidDiagram } from "./MermaidDiagram";


describe("MermaidDiagram", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    initialize.mockClear();
    render.mockReset();
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

  it("renders a sanitized local Mermaid diagram in strict mode", async () => {
    render.mockResolvedValue({ svg: '<svg onload="evil()"><text>ok</text><script>bad()</script></svg>' });

    await act(async () => root.render(<MermaidDiagram source="graph TD; A-->B" />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(initialize).toHaveBeenCalledWith(expect.objectContaining({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
    }));
    expect(container.querySelector("svg")?.textContent).toContain("ok");
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("onload");
  });

  it("falls back to source when only one diagram fails", async () => {
    render.mockRejectedValue(new Error("bad diagram"));

    await act(async () => root.render(<MermaidDiagram source="graph broken" />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.textContent).toContain("图表暂时无法渲染");
    expect(container.textContent).toContain("graph broken");
  });
});
