import type { KeyboardEvent as ReactKeyboardEvent } from "react";

const FOCUSABLE = "button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex='-1'])";

function isInActiveDialogTree(element: HTMLElement): boolean {
  return element.closest("[hidden],[inert],[aria-hidden='true']") === null;
}

export function trapDialogFocus(event: ReactKeyboardEvent<HTMLElement>, close: () => void): void {
  if (event.key === "Escape") { event.preventDefault(); close(); return; }
  if (event.key !== "Tab") return;
  const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE)]
    .filter(isInActiveDialogTree);
  if (focusable.length === 0) { event.preventDefault(); return; }
  const first = focusable[0]; const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
