/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HrPositionMaterialItem } from "../../hrR12Types";
import { HrPositionTaskMenu } from "./HrPositionTaskMenu";

const material: HrPositionMaterialItem = {
  attachmentId: "55555555-5555-4555-8555-555555555555",
  filename: "岗位说明.pdf",
  mediaType: "application/pdf",
  state: "ready",
  sizeBytes: 10,
  createdAt: "2026-09-04T00:00:00Z",
  sourceConversationId: null,
  sourceTurnId: null,
  previewAvailable: true,
  downloadAvailable: true,
};

describe("HrPositionTaskMenu", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("starts a real task directly and closes without changing composer text", async () => {
    const onStart = vi.fn();
    const onComposerChange = vi.fn();
    await act(async () => root.render(<div onInput={onComposerChange}><HrPositionTaskMenu
      disabled={false} materials={[material]} selectedMaterialIds={[]}
      onSelectedMaterialIdsChange={vi.fn()} onStart={onStart}
    /></div>));
    const opener = container.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!;
    await act(async () => opener.click());
    const jd = [...container.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent?.includes("生成岗位说明"))!;
    await act(async () => jd.click());

    expect(onStart).toHaveBeenCalledWith("jd");
    expect(onComposerChange).not.toHaveBeenCalled();
    expect(container.querySelector('[role="menu"]')).toBeNull();
  });

  it("selects task materials explicitly and restores opener focus on Escape", async () => {
    const onSelectedMaterialIdsChange = vi.fn();
    await act(async () => root.render(<HrPositionTaskMenu
      disabled={false} materials={[material]} selectedMaterialIds={[]}
      onSelectedMaterialIdsChange={onSelectedMaterialIdsChange} onStart={vi.fn()}
    />));
    const opener = container.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!;
    await act(async () => opener.click());
    const checkbox = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    expect(checkbox.checked).toBe(false);
    await act(async () => checkbox.click());
    expect(onSelectedMaterialIdsChange).toHaveBeenCalledWith([material.attachmentId]);

    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(container.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("disables the only task entry point in read-only mode", async () => {
    await act(async () => root.render(<HrPositionTaskMenu
      disabled materials={[]} selectedMaterialIds={[]}
      onSelectedMaterialIdsChange={vi.fn()} onStart={vi.fn()}
    />));
    expect(container.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')?.disabled).toBe(true);
    expect(container.querySelector('[role="menu"]')).toBeNull();
  });
});
