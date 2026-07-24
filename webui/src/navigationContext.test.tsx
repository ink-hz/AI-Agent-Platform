/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlatformLink } from "./components/PlatformLink";
import {
  captureSessionOrigin,
  sessionReturnTarget,
  useHistoryScrollRestoration,
} from "./navigationContext";


function Restorer({ ready }: { ready: boolean }) {
  useHistoryScrollRestoration(ready);
  return null;
}


describe("Session navigation context", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    window.history.replaceState({}, "", "/");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("captures the exact internal URL and scroll position", () => {
    window.history.replaceState({}, "", "/sessions?agent_id=ai-fae-agent&q=Gemini");

    const state = captureSessionOrigin(640);

    expect(state).toEqual({
      sessionOrigin: {
        path: "/sessions?agent_id=ai-fae-agent&q=Gemini",
        scrollY: 640,
      },
    });
    expect(window.history.state).toEqual(state);
    expect(sessionReturnTarget(state)).toBe("/sessions?agent_id=ai-fae-agent&q=Gemini");
  });

  it("rejects external, protocol-relative, and unsupported return targets", () => {
    expect(sessionReturnTarget({ sessionOrigin: { path: "https://example.com", scrollY: 10 } })).toBeNull();
    expect(sessionReturnTarget({ sessionOrigin: { path: "//example.com", scrollY: 10 } })).toBeNull();
    expect(sessionReturnTarget({ sessionOrigin: { path: "/unknown", scrollY: 10 } })).toBeNull();
  });

  it("waits for content readiness before restoring a matching source entry", async () => {
    window.history.replaceState({
      sessionOrigin: { path: "/sessions?agent_id=ai-fae-agent", scrollY: 640 },
    }, "", "/sessions?agent_id=ai-fae-agent");
    Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, value: 2000 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      callback(0);
      return 1;
    });

    await act(async () => root.render(<Restorer ready={false} />));
    expect(scrollTo).not.toHaveBeenCalled();

    await act(async () => root.render(<Restorer ready />));
    expect(scrollTo).toHaveBeenCalledWith(0, 640);
  });

  it("stores source context when a Session drill-down link is followed", async () => {
    window.history.replaceState({}, "", "/sessions?source_kind=fae");
    Object.defineProperty(window, "scrollY", { configurable: true, value: 420 });
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    await act(async () => root.render(
      <PlatformLink href="/sessions/fae%3Aone" preserveSessionContext>Open Session</PlatformLink>,
    ));

    await act(async () => container.querySelector("a")?.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    ));

    expect(`${window.location.pathname}${window.location.search}`).toBe("/sessions/fae%3Aone");
    expect(window.history.state).toEqual({
      sessionOrigin: { path: "/sessions?source_kind=fae", scrollY: 420 },
    });
  });
});
