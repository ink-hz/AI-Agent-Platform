/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { copyVisibleText } from "./clipboard";

afterEach(() => vi.restoreAllMocks());

describe("copyVisibleText", () => {
  it("uses the clipboard API when it is available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    await expect(copyVisibleText("可见回答")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("可见回答");
  });

  it("falls back to a temporary textarea when clipboard access fails", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    const execute = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", { configurable: true, value: execute });

    await expect(copyVisibleText("只复制用户看得到的文字")).resolves.toBe(true);
    expect(execute).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull();
  });
});
