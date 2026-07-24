/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PLATFORM_TITLE, routeDocumentTitle, useDocumentTitle } from "./documentTitle";


function TitleProbe({ title }: { title: string }) {
  useDocumentTitle(title);
  return null;
}


describe("document titles", () => {
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    document.title = "";
  });

  it("uses contextual Orbbec Agent Platform titles", () => {
    expect(routeDocumentTitle({ name: "overview" })).toBe("Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "agents" })).toBe("Agent · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "agent", agentId: "one" })).toBe("Agent 详情 · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "agent-runtime", agentId: "one" })).toBe("运行详情 · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "sessions" })).toBe("Session · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "session", sessionKey: "one" })).toBe("Session 回放 · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "activity" })).toBe("运行记录 · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "flywheel" })).toBe(PLATFORM_TITLE);
    expect(routeDocumentTitle({ name: "not-found" })).toBe(PLATFORM_TITLE);
  });

  it("updates the browser title when asynchronous content changes", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);

    await act(async () => root.render(<TitleProbe title="Agent · Orbbec Agent Platform" />));
    expect(document.title).toBe("Agent · Orbbec Agent Platform");

    await act(async () => root.render(<TitleProbe title="AI FAE · Orbbec Agent Platform" />));
    expect(document.title).toBe("AI FAE · Orbbec Agent Platform");
    await act(async () => root.unmount());
  });
});
