/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MermaidLightbox } from "./MermaidLightbox";


function pointer(type: string, pointerId: number, clientX: number, clientY: number): Event {
  const event = new Event(type, { bubbles: true });
  Object.defineProperties(event, {
    pointerId: { value: pointerId },
    clientX: { value: clientX },
    clientY: { value: clientY },
  });
  return event;
}


describe("MermaidLightbox", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let onClose: ReturnType<typeof vi.fn>;
  const showModal = vi.fn(function show(this: HTMLDialogElement) {
    Object.defineProperty(this, "open", { configurable: true, value: true });
  });
  const close = vi.fn(function closeDialog(this: HTMLDialogElement) {
    Object.defineProperty(this, "open", { configurable: true, value: false });
  });

  beforeEach(() => {
    showModal.mockClear();
    close.mockClear();
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: showModal });
    Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: close });
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", { configurable: true, value: vi.fn() });
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", { configurable: true, value: vi.fn(() => true) });
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", { configurable: true, value: vi.fn() });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    onClose = vi.fn();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.style.overflow = "";
    for (const name of ["showModal", "close"]) Reflect.deleteProperty(HTMLDialogElement.prototype, name);
    for (const name of ["setPointerCapture", "hasPointerCapture", "releasePointerCapture"]) {
      Reflect.deleteProperty(HTMLElement.prototype, name);
    }
  });

  function button(name: string): HTMLButtonElement {
    const result = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => candidate.getAttribute("aria-label") === name || candidate.textContent === name);
    if (!result) throw new Error(`missing button: ${name}`);
    return result;
  }

  async function renderLightbox(description: string | null = "从问题到答案的检索过程。") {
    await act(async () => root.render(<MermaidLightbox
      description={description}
      imageSource="data:image/svg+xml,diagram"
      onClose={onClose}
      title="RAG 查询链路"
    />));
  }

  it("opens as a modal, exposes its description, and restores body scrolling", async () => {
    document.body.style.overflow = "clip";
    await renderLightbox();

    const dialog = container.querySelector("dialog")!;
    const descriptionId = dialog.getAttribute("aria-describedby")!;
    expect(showModal).toHaveBeenCalledOnce();
    expect(dialog.getAttribute("aria-label")).toBe("RAG 查询链路");
    expect(container.querySelector(`#${descriptionId}`)?.textContent).toBe("从问题到答案的检索过程。");
    expect(document.body.style.overflow).toBe("hidden");

    await act(async () => root.unmount());
    expect(document.body.style.overflow).toBe("clip");
    root = createRoot(container);
  });

  it("clamps zoom, pans only when enlarged, and resets the view", async () => {
    await renderLightbox(null);
    const canvas = container.querySelector<HTMLElement>(".mermaid-lightbox-canvas")!;
    const image = container.querySelector<HTMLImageElement>(".mermaid-lightbox-image")!;

    await act(async () => {
      canvas.dispatchEvent(pointer("pointerdown", 6, 10, 10));
      canvas.dispatchEvent(pointer("pointermove", 6, 35, 25));
      canvas.dispatchEvent(pointer("pointerup", 6, 35, 25));
    });
    expect(image.style.transform).toBe("translate(0px, 0px) scale(1)");

    await act(async () => button("放大").click());
    expect(image.style.transform).toContain("scale(1.25)");
    await act(async () => {
      canvas.dispatchEvent(pointer("pointerdown", 7, 10, 10));
      canvas.dispatchEvent(pointer("pointermove", 7, 35, 25));
      canvas.dispatchEvent(pointer("pointerup", 7, 35, 25));
    });
    expect(image.style.transform).toContain("translate(25px, 15px)");

    await act(async () => button("恢复").click());
    expect(image.style.transform).toBe("translate(0px, 0px) scale(1)");
    expect(button("缩小").disabled).toBe(true);

    for (let index = 0; index < 20; index += 1) await act(async () => button("放大").click());
    expect(container.querySelector("output")?.textContent).toBe("400%");
    expect(button("放大").disabled).toBe(true);
    for (let index = 0; index < 20; index += 1) await act(async () => button("缩小").click());
    expect(container.querySelector("output")?.textContent).toBe("100%");
  });

  it("closes from cancel and the explicit control", async () => {
    await renderLightbox();
    const cancel = new Event("cancel", { cancelable: true });
    await act(async () => container.querySelector("dialog")!.dispatchEvent(cancel));
    expect(cancel.defaultPrevented).toBe(true);
    expect(onClose).toHaveBeenCalledOnce();

    await act(async () => button("关闭大图").click());
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
