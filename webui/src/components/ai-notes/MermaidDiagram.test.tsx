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
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value: vi.fn(function show(this: HTMLDialogElement) {
        Object.defineProperty(this, "open", { configurable: true, value: true });
      }),
    });
    Object.defineProperty(HTMLDialogElement.prototype, "close", {
      configurable: true,
      value: vi.fn(function close(this: HTMLDialogElement) {
        Object.defineProperty(this, "open", { configurable: true, value: false });
      }),
    });
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.style.overflow = "";
    Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
    Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
    vi.restoreAllMocks();
  });

  async function settleMermaid() {
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  }

  function button(name: string): HTMLButtonElement {
    const result = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => candidate.getAttribute("aria-label") === name || candidate.textContent === name);
    if (!result) throw new Error(`missing button: ${name}`);
    return result;
  }

  it("renders a sanitized local Mermaid diagram in strict mode", async () => {
    render.mockResolvedValue({ svg: '<svg onload="evil()"><text>ok</text><script>bad()</script></svg>' });

    await act(async () => root.render(<MermaidDiagram source="graph TD; A-->B" />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(initialize).toHaveBeenCalledWith(expect.objectContaining({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      themeVariables: {
        background: "#FFFFFF",
        clusterBkg: "#FFFFFF",
        clusterBorder: "#CBD5E1",
      },
      htmlLabels: false,
      flowchart: { htmlLabels: false },
    }));
    const diagram = container.querySelector<HTMLImageElement>('img[alt="Mermaid 图表"]');
    const encoded = diagram?.getAttribute("src")?.split(",", 2)[1] ?? "";
    const sanitized = decodeURIComponent(encoded);
    expect(sanitized).toContain("<text>ok</text>");
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(sanitized).not.toContain("onload");
    expect(sanitized).not.toContain("<script");
  });

  it("falls back to source when only one diagram fails", async () => {
    render.mockRejectedValue(new Error("bad diagram"));

    await act(async () => root.render(<MermaidDiagram source="graph broken" />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(container.textContent).toContain("图表暂时无法渲染");
    expect(container.textContent).toContain("graph broken");
  });

  it("uses Mermaid metadata and opens the rendered image without rendering twice", async () => {
    render.mockResolvedValue({ svg: "<svg><text>ok</text></svg>" });
    await act(async () => root.render(<MermaidDiagram source={`flowchart LR
      accTitle: Agent 行动循环
      accDescr: Agent 在目标、工具结果和完成证据之间循环。
      A-->B`} />));
    await settleMermaid();

    const trigger = container.querySelector<HTMLButtonElement>(".mermaid-diagram-trigger")!;
    expect(trigger.getAttribute("aria-label")).toBe("查看大图：Agent 行动循环");
    expect(trigger.querySelector("img")?.alt).toBe("Agent 行动循环");
    expect(trigger.querySelector(".mermaid-diagram-zoom-hint")).toBeNull();
    expect(trigger.textContent).not.toContain("查看大图");

    await act(async () => trigger.click());
    expect(container.querySelector("dialog")?.getAttribute("aria-label")).toBe("Agent 行动循环");
    expect(render).toHaveBeenCalledTimes(1);

    await act(async () => container.querySelector<HTMLImageElement>(".mermaid-lightbox-image")!.click());
    expect(container.querySelector("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);

    await act(async () => trigger.click());
    const cancel = new Event("cancel", { cancelable: true });
    await act(async () => container.querySelector("dialog")!.dispatchEvent(cancel));
    expect(cancel.defaultPrevented).toBe(true);
    expect(container.querySelector("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(render).toHaveBeenCalledTimes(1);
  });

  it("keeps the inline diagram readable when modal dialogs are unsupported", async () => {
    render.mockResolvedValue({ svg: "<svg><text>ok</text></svg>" });
    await act(async () => root.render(<MermaidDiagram source="flowchart LR; A-->B" />));
    await settleMermaid();
    Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");

    const trigger = container.querySelector<HTMLButtonElement>(".mermaid-diagram-trigger")!;
    await act(async () => trigger.click());

    expect(container.querySelector("dialog")).toBeNull();
    expect(trigger.querySelector("img")).not.toBeNull();
  });
});
