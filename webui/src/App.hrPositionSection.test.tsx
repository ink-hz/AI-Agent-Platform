/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { LegacyRedirect } from "./App";

let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("replaces the whole document for an HR deep link and keeps its query", async () => {
  window.history.replaceState({}, "", "/hr/positions/00000000-0000-4000-8000-000000000001/candidates?view=current&from=directory");
  const replace = vi.fn();
  await act(async () => root.render(
    <LegacyRedirect
      to="/hr/positions/00000000-0000-4000-8000-000000000001/candidates"
      navigation="document"
      location={{ replace }}
    />,
  ));
  expect(replace).toHaveBeenCalledOnce();
  expect(replace).toHaveBeenCalledWith(
    "/hr/positions/00000000-0000-4000-8000-000000000001/candidates?view=current&from=directory",
  );
});

it("does not statically import a platform-owned HR workspace from App", () => {
  const source = readFileSync("src/App.tsx", "utf8");
  expect(source).not.toMatch(/workspaces\/hr\/Hr(?:LoopWorkspace|WorkspacePage)/);
});
