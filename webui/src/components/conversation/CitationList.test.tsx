/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CitationList } from "./CitationList";

describe("CitationList", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("keeps numbered sources collapsed until requested", async () => {
    await act(async () => root.render(<CitationList citations={[{
      citationKey: "source-1", title: "联合光电招聘", url: "https://example.com/jobs",
      site: "example.com", retrievedAt: "2026-09-03T10:00:00Z", supports: ["研发岗位"],
    }]} />));
    expect(container.textContent).toContain("来源（1）");
    expect(container.querySelector("a")).toBeNull();
    await act(async () => container.querySelector("button")?.click());
    expect(container.textContent).toContain("[1]");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://example.com/jobs");
  });
});
