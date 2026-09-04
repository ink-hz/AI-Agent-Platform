/** @vitest-environment jsdom */

import { afterEach, expect, it, vi } from "vitest";

import { trapDialogFocus } from "./modalFocus";

afterEach(() => { document.body.replaceChildren(); });

it("ignores focusable nodes inside hidden, inert, and aria-hidden ancestors", () => {
  const dialog = document.createElement("div");
  const first = document.createElement("button"); first.textContent = "first";
  const last = document.createElement("button"); last.textContent = "last";
  dialog.append(first, last);
  for (const attribute of ["hidden", "inert", "aria-hidden"] as const) {
    const branch = document.createElement("section");
    branch.setAttribute(attribute, attribute === "aria-hidden" ? "true" : "");
    branch.append(document.createElement("button"));
    dialog.append(branch);
  }
  document.body.append(dialog);

  first.focus();
  const backward = { key: "Tab", shiftKey: true, currentTarget: dialog, preventDefault: vi.fn(), stopPropagation: vi.fn() };
  trapDialogFocus(backward as never, vi.fn());
  expect(backward.preventDefault).toHaveBeenCalledTimes(1);
  expect(document.activeElement).toBe(last);

  last.focus();
  const forward = { key: "Tab", shiftKey: false, currentTarget: dialog, preventDefault: vi.fn(), stopPropagation: vi.fn() };
  trapDialogFocus(forward as never, vi.fn());
  expect(forward.preventDefault).toHaveBeenCalledTimes(1);
  expect(document.activeElement).toBe(first);
});

it("owns handled keyboard events so a parent dialog cannot handle them again", () => {
  const dialog = document.createElement("div");
  const onlyButton = document.createElement("button");
  dialog.append(onlyButton);
  document.body.append(dialog);
  onlyButton.focus();

  const escape = {
    key: "Escape", shiftKey: false, currentTarget: dialog,
    defaultPrevented: false, preventDefault: vi.fn(function (this: { defaultPrevented: boolean }) { this.defaultPrevented = true; }),
    stopPropagation: vi.fn(),
  };
  const close = vi.fn();
  trapDialogFocus(escape as never, close);
  expect(close).toHaveBeenCalledTimes(1);
  expect(escape.stopPropagation).toHaveBeenCalledTimes(1);

  const outerClose = vi.fn();
  trapDialogFocus(escape as never, outerClose);
  expect(outerClose).not.toHaveBeenCalled();

  const tab = {
    key: "Tab", shiftKey: false, currentTarget: dialog,
    defaultPrevented: false, preventDefault: vi.fn(function (this: { defaultPrevented: boolean }) { this.defaultPrevented = true; }),
    stopPropagation: vi.fn(),
  };
  trapDialogFocus(tab as never, vi.fn());
  expect(tab.stopPropagation).toHaveBeenCalledTimes(1);
});
